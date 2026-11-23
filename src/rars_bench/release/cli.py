"""`rars-bench release` and `rars-bench release-compare`."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from . import discmaster, formats, report
from .inputs import Inputs
from .measure import banner, git_commit, sha256_file
from .scenarios import SCENARIOS, Tools, run_group

GIB, MIB = 1 << 30, 1 << 20

SCALES = {
    # About an hour on a quiet 32-thread machine once the inputs are cached; see the PR.
    # 32 MiB per class: rars writes level 5 at about 1 MB/s on one thread, so
    # 64 MiB put a quarter of an hour into that one row.
    "full": {
        "volume_bytes": 2 * GIB, "volume_size": "100m", "big_bytes": GIB, "small_files": 20000,
        "class_bytes": 32 * MIB, "random_bytes": GIB, "read_threads": [1, 4, 16],
        "create_levels": [1, 3, 5], "create_threads": lambda many: [1, many],
        "random_cells": lambda many: [(many, [1, 3, 5]), (1, [3])],
        "rounds": 3, "dm_count": 3000, "dm_max_bytes": 16 * MIB, "dm_seed": 368, "dm_rounds": 2,
    },
    # Seconds per scenario: proves the runner end to end, measures nothing.
    "tiny": {
        "volume_bytes": 8 * MIB, "volume_size": "1m", "big_bytes": 8 * MIB, "small_files": 200,
        "class_bytes": 2 * MIB, "random_bytes": 8 * MIB, "read_threads": [1, 2],
        "create_levels": [1, 5], "create_threads": lambda many: [1, many],
        "random_cells": lambda many: [(many, [3]), (1, [3])],
        "rounds": 2, "dm_count": 20, "dm_max_bytes": MIB, "dm_seed": 368, "dm_rounds": 1,
    },
}

TIMED = list(SCENARIOS)
ALL = TIMED + ["discmaster", "formats"]


@dataclass
class Ctx:
    tools: Tools
    inputs: Inputs
    scale: dict
    rounds: int
    timeout: float
    dm_timeout: float
    run_dir: Path
    scratch_root: Path
    scratch: Path = field(default=None)
    log: object = print


def add_parsers(sub) -> None:
    here = Path.cwd()
    rar_repo = Path(os.environ.get("RAR_REPO", Path.home() / "src/tmp/rar"))

    def ref(name: str) -> Path:
        local = here / "_refs/rarbins" / name
        return local if local.is_file() else rar_repo / "_refs/rarbins" / name

    r = sub.add_parser("release", help="the release bench: speed, memory, size and correctness against RAR")
    r.add_argument("--rars-bin", type=Path, required=True, help="the build under test; pin it")
    r.add_argument("--rars-commit", default=None, help="override the commit read from the binary's checkout")
    r.add_argument("--rar-bin", type=Path, default=ref("rar712-linux-x64"), help="native rar 7.12")
    r.add_argument("--rar393-bin", type=Path, default=ref("rar393-linux-x64"), help="native rar 3.93")
    r.add_argument("--unrar-bin", type=Path, default=Path("/usr/bin/unrar"))
    r.add_argument("--rarfast-bin", type=Path, default=here / "_refs/rarfast/rarfast")
    r.add_argument("--scale", choices=sorted(SCALES), default="full")
    r.add_argument("--rounds", type=int, default=None, help="override the scale's rounds")
    r.add_argument("--only", default=None, help=f"comma list from: {','.join(ALL)}")
    r.add_argument("--skip", default="", help="comma list of scenarios to leave out")
    r.add_argument("--prepare", action="store_true",
                   help="build the inputs, reference archives and DiscMaster sample, then stop; times nothing")
    r.add_argument("--threads", type=int, default=None,
                   help="the 'many threads' count (default: CPUs this process may use)")
    r.add_argument("--cache", type=Path, default=Path(".release/cache"), help="generated inputs, kept between runs")
    r.add_argument("--scratch", type=Path, default=Path(".release/scratch"), help="outputs; emptied per scenario")
    r.add_argument("--out-dir", type=Path, default=Path("results"))
    r.add_argument("--timeout", type=float, default=1800.0, help="seconds per timed command")
    r.add_argument("--discmaster-root", type=Path, default=Path.home() / "src/rars-data",
                   help="holds collected/manifest.tsv and the extracted/ tree it names; read only")
    r.add_argument("--formats", default=None, help="format sweep: comma list (default: every format)")
    r.add_argument("--sweep-classes", default=None, help="format sweep: corpus classes (default: per format)")
    r.add_argument("--corpora-root", type=Path, default=Path("corpora"))
    r.add_argument("--rar1402-bin", type=Path, default=Path("_refs/rarbins/RAR1_402.EXE"))
    r.add_argument("--rar250-bin", type=Path, default=Path("_refs/rarbins/RAR250.EXE"))
    r.add_argument("--wineprefix-root", type=Path, default=Path("_refs/wineprefixes"))
    r.add_argument("--cache-root", type=Path, default=Path("_refs/cache"), help="oracle encode cache")
    r.add_argument("--no-oracle-cache", action="store_true")

    c = sub.add_parser("release-compare", help="markdown diff of two release-bench JSON files")
    c.add_argument("old", type=Path)
    c.add_argument("new", type=Path)
    c.add_argument("--out", type=Path, default=None)
    c.add_argument("--threshold", type=float, default=0.05)


def _optional(path: Path | None) -> Path | None:
    return path.resolve() if path is not None and path.is_file() and os.access(path, os.X_OK) else None


def describe_tools(args, tools: Tools) -> dict:
    out = {}
    rars = {"path": str(tools.rars), "sha256": sha256_file(tools.rars),
            "version": banner([str(tools.rars), "--version"]), **git_commit(tools.rars)}
    if args.rars_commit:
        rars.update(commit=args.rars_commit, subject="(given with --rars-commit)", dirty=False)
    out["rars"] = rars
    for name, cmd in (("rar", [str(tools.rar)] if tools.rar else None),
                      ("rar393", [str(tools.rar393)] if tools.rar393 else None),
                      ("unrar", [str(tools.unrar)] if tools.unrar else None),
                      ("rarfast", [str(tools.rarfast), "--version"] if tools.rarfast else None)):
        if cmd is None:
            out[name] = {"version": "not found"}
            continue
        path = Path(cmd[0])
        out[name] = {"path": str(path), "sha256": sha256_file(path), "version": banner(cmd)}
    # The sweep's references, which only ever give sizes and verdicts.
    for fmt, cfg in __import__("rars_bench.harness", fromlist=["_WINE_ORACLES"])._WINE_ORACLES.items():
        exe = args.wineprefix_root / cfg.prefix_subdir / cfg.rar_relpath
        out[f"sweep-{fmt}"] = {"version": f"WinRAR {cfg.version} (wine; sizes only)", "path": str(exe),
                               "sha256": sha256_file(exe) if exe.is_file() else "missing"}
    for fmt, exe, label in (("rar14", args.rar1402_bin, "RAR 1.402"), ("rar20", args.rar250_bin, "RAR 2.50")):
        out[f"sweep-{fmt}"] = {"version": f"{label} (DOSBox-X; sizes only)", "path": str(exe),
                               "sha256": sha256_file(exe) if exe.is_file() else "missing"}
    for name, cmd in (("wine", ["wine", "--version"]), ("dosbox-x", ["dosbox-x", "-version"])):
        if shutil.which(cmd[0]):
            out[name] = {"version": banner(cmd, r".")}
    return out


def run(args: argparse.Namespace) -> int:
    rars = _optional(args.rars_bin)
    if rars is None:
        print(f"error: no rars binary at {args.rars_bin}", file=sys.stderr)
        return 2
    scale = SCALES[args.scale]
    rar = _optional(args.rar_bin)
    tools = Tools(rars=rars, rar=rar, rar_sha=sha256_file(rar) if rar else "", rar393=_optional(args.rar393_bin),
                  unrar=_optional(args.unrar_bin), rarfast=_optional(args.rarfast_bin),
                  many=args.threads or len(os.sched_getaffinity(0)))
    chosen = args.only.split(",") if args.only else list(ALL)
    chosen = [s for s in chosen if s not in set(filter(None, args.skip.split(",")))]
    unknown = set(chosen) - set(ALL)
    if unknown:
        print(f"error: unknown scenario(s) {sorted(unknown)}; choose from {ALL}", file=sys.stderr)
        return 2
    if tools.rar is None and set(chosen) & set(TIMED):
        print(f"error: native rar 7.12 not found at {args.rar_bin}; the timed scenarios need it", file=sys.stderr)
        return 2

    started = time.time()
    meta = {"date": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "scale": args.scale,
            "scale_params": {k: v for k, v in scale.items() if not callable(v)},
            "rounds": args.rounds or scale["rounds"], "threads_available": tools.many,
            "host": {"kernel": platform.release(), "python": platform.python_version(),
                     "cpus": os.cpu_count()},
            "tools": describe_tools(args, tools), "scenarios_run": chosen}
    commit = (meta["tools"]["rars"].get("commit") or meta["tools"]["rars"]["sha256"])[:8]
    stem = f"release-{meta['date'][:10]}-{commit}-{args.scale}"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    run_dir = args.out_dir / stem
    if not args.prepare:
        run_dir.mkdir(exist_ok=True)
    ctx = Ctx(tools=tools, inputs=Inputs(args.cache.resolve(), scale), scale=scale, rounds=meta["rounds"],
              timeout=args.timeout, dm_timeout=min(args.timeout, 300.0), run_dir=run_dir,
              scratch_root=args.scratch.resolve())
    result = {"meta": meta, "scenarios": [], "cells_tsv": str(run_dir / "format-cells.tsv")}
    json_path, md_path = args.out_dir / f"{stem}.json", args.out_dir / f"{stem}.md"

    for name in chosen:
        print(f"[{time.time() - started:7.0f}s] {name}", flush=True)
        ctx.scratch = ctx.scratch_root / name
        shutil.rmtree(ctx.scratch, ignore_errors=True)
        ctx.scratch.mkdir(parents=True)
        entry = {"name": name}
        t0 = time.time()
        try:
            if args.prepare:
                if name in SCENARIOS:
                    entry["info"] = SCENARIOS[name](ctx)[0]
                elif name == "discmaster":
                    picked = discmaster.sample(args.discmaster_root, args.discmaster_root / "collected/manifest.tsv",
                                               scale["dm_count"], scale["dm_max_bytes"], scale["dm_seed"],
                                               ctx.inputs.root)
                    entry["info"] = {"archives": len(picked), "bytes": sum(p["size"] for p in picked)}
                print(f"    prepared {entry.get('info', {})}"[:300], flush=True)
            elif name in SCENARIOS:
                fn = SCENARIOS[name]
                entry["doc"] = fn.__doc__.strip().splitlines()[0]
                info, groups = fn(ctx)
                entry["info"] = info
                entry["groups"] = []
                for group in groups:
                    results = run_group(group, ctx)
                    entry["groups"].append({"name": group.name, "attrs": group.attrs, "results": results})
                    rars = next(r for r in results if r["tool"] == "rars")
                    ref = next((r for r in results if r["reference"]), None)
                    print(f"    {group.name:24} rars {rars['wall_min_s']:8.3f}s"
                          + (f"  {ref['tool']} {ref['wall_min_s']:8.3f}s" if ref else "")
                          + ("" if all(r["ok"] for r in results) else "  FAILURES"), flush=True)
            elif name == "discmaster":
                entry["doc"] = discmaster.__doc__.strip().splitlines()[0]
                manifest = args.discmaster_root / "collected/manifest.tsv"
                if not manifest.is_file():
                    raise FileNotFoundError(f"no DiscMaster manifest at {manifest}")
                entry["discmaster"] = discmaster.run(ctx, args.discmaster_root, manifest)
            elif name == "formats":
                entry["doc"] = formats.__doc__.strip().splitlines()[0]
                entry["sweep"] = formats.run(ctx, args)
                formats.write_tsv(entry["sweep"]["cells"], run_dir / "format-cells.tsv")
        except Exception as e:  # one broken scenario must not lose the others
            entry["error"] = f"{type(e).__name__}: {e}"
            entry["traceback"] = traceback.format_exc()
            print(f"    FAILED: {entry['error']}", flush=True)
        entry["elapsed_s"] = round(time.time() - t0, 1)
        result["scenarios"].append(entry)
        shutil.rmtree(ctx.scratch, ignore_errors=True)
        meta["elapsed_s"] = time.time() - started
        if not args.prepare:
            json_path.write_text(json.dumps(result, indent=1, default=str))

    meta["elapsed_s"] = time.time() - started
    if args.prepare:
        print(f"prepared inputs in {args.cache} in {meta['elapsed_s'] / 60:.1f} min")
        return 1 if any("error" in s for s in result["scenarios"]) else 0
    json_path.write_text(json.dumps(result, indent=1, default=str))
    report.write(md_path, report.render(result))
    print(f"wrote {json_path} and {md_path} in {meta['elapsed_s'] / 60:.1f} min")
    return 1 if any("error" in s for s in result["scenarios"]) else 0


def run_compare(args: argparse.Namespace) -> int:
    old, new = (json.loads(p.read_text()) for p in (args.old, args.new))
    text = report.compare(old, new, args.threshold)
    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text, end="")
    return 0
