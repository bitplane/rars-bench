"""`rars-bench quick`: the deterministic tier. Sizes, verdicts and invariants, no timings.

For every format rars writes, at levels 1 to 5, solid and not, over the
corpus classes, it records:

- rars' packed bytes against the format's era reference, which comes from
  the format sweep's oracle cache (`_refs/cache`) and is encoded once under
  wine or DOSBox only when missing;
- `unrar t` on every archive rars wrote;
- the level ladder: a cell where level n packs larger than level n-1;
- thread identity: the same input at `--threads 1` and at `tmax` must give
  the same archive bytes.

Nothing is timed, so the tier is safe on a loaded machine and runs rars'
cells side by side, each pinned to its own CPU (never CPU 0). Any broken
ladder, identity or correctness check is listed and exits 1. The result goes
into the history store as `<machine-id>-quick.json`.

The DOS references (RAR 1.402, RAR 2.50) are driven one member at a time,
which cannot write a solid archive, so their solid cells carry rars' size
alone. RAR 1.402 has no levels: its one archive is the reference for every
level. RAR 1.5 has no reference encoder at all.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import cache as _cache
from . import registry
from .corpus import enumerate_class
from .drivers.base import spawn
from .history import machine as _machine
from .history import store
from .packed import packed_bytes
from .variants import RAR14_SLICE, Variant

FORMATS = ["rar14", "rar15", "rar20", "rar29", "rar30", "rar40", "rar50", "rar70"]
LEVELS = [1, 2, 3, 4, 5]
#: Formats whose reference runs under DOSBox: the small tier only, and no solid reference.
DOS_FORMATS = {"rar14", "rar20"}
#: Class-name prefixes the identity check runs on by default: big enough for rars to split work.
IDENTITY_PREFIXES = ("large-", "edge-")


@dataclass(frozen=True)
class Cell:
    fmt: str
    solid: bool
    level: int
    cls: str

    @property
    def id(self) -> str:
        return store.size_cell_id(self.fmt, self.solid, self.level, self.cls)

    @property
    def variant(self) -> Variant:
        return Variant(format=self.fmt, method=f"lz-{self.level}", method_int=self.level, filter="none",
                       solid=self.solid, encrypted=False, password=None, dict_log=None, volume_size=None)


def classes_for(fmt: str, present: list[str]) -> list[str]:
    """DOS-era formats skip the large tier: their references time out on it, and RAR 1.4/2.0 gain nothing there."""
    if fmt in DOS_FORMATS:
        return [c for c in present if not c.startswith("large-")]
    return list(present)


def reference_variant(cell: Cell) -> Variant | None:
    """The reference encode a cell is scored against, or None when there is none."""
    if cell.fmt == "rar15":
        return None
    if cell.fmt in DOS_FORMATS and (cell.solid or cell.cls.startswith(("large-", "edge-"))):
        return None
    if cell.fmt == "rar14":
        return RAR14_SLICE[0]
    return cell.variant


# --- the checks ----------------------------------------------------------------------------------

def ladder_breaks(cells: dict[str, dict]) -> list[dict]:
    """Cells where level n packs larger than level n-1, for the same format, solidity and class."""
    series: dict[tuple, dict[int, int]] = {}
    for cid, cell in cells.items():
        _, fmt, solid, level, cls = cid.split("/", 4)
        size = cell["metrics"].get("packed") or cell["metrics"].get("archive")
        if size:
            series.setdefault((fmt, solid, cls), {})[int(level[1:])] = size
    flags = []
    for (fmt, solid, cls), by_level in sorted(series.items()):
        for level in sorted(by_level):
            below = by_level.get(level - 1)
            if below is not None and by_level[level] > below:
                flags.append({"check": "ladder", "cell": store.size_cell_id(fmt, solid == "solid", level, cls),
                              "detail": f"m{level} {by_level[level]} > m{level - 1} {below} bytes "
                                        f"(+{by_level[level] / below - 1:.2%})"})
    return flags


def identity_breaks(cells: dict[str, dict]) -> list[dict]:
    flags = []
    for cid, cell in sorted(cells.items()):
        ident = cell["metrics"].get("identity")
        if ident and not ident["same"]:
            flags.append({"check": "identity", "cell": cid,
                          "detail": f"t1 {cell['metrics']['archive']} bytes, {ident['label']} "
                                    f"{ident['archive']} bytes, contents differ"})
    return flags


def correctness_breaks(cells: dict[str, dict]) -> list[dict]:
    flags = []
    for cid, cell in sorted(cells.items()):
        m = cell["metrics"]
        if m["status"] != "ok":
            flags.append({"check": "correctness", "cell": cid, "detail": m["status"]})
        ident = m.get("identity")
        if ident and ident.get("status", "ok") != "ok":
            flags.append({"check": "correctness", "cell": cid, "detail": f"{ident['label']}: {ident['status']}"})
    return flags


def all_flags(cells: dict[str, dict]) -> list[dict]:
    return correctness_breaks(cells) + identity_breaks(cells) + ladder_breaks(cells)


# --- running cells -------------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_rars(binary: Path, cell: Cell, items: list, out: Path, threads: int, cpus: str,
             unrar: Path | None, timeout: float) -> dict:
    """One rars archive of the class, its packed bytes, hash and unrar's verdict."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    base = Path(os.path.commonpath([i.abs_path.resolve().parent for i in items]))
    names = [str(i.abs_path.resolve().relative_to(base)) for i in items]
    cmd = [str(binary), "--threads", str(threads), "a", "--format", cell.fmt, "--level", str(cell.level)]
    cmd += ["--solid"] if cell.solid else []
    cmd += [str(out.resolve()), *names]
    if shutil.which("taskset"):
        cmd = ["taskset", "-c", cpus, *cmd]
    with (out.parent / f"{out.stem}.log").open("wb") as log:
        done = spawn(cmd, cwd=base, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    if done.timed_out or done.exit != 0 or not out.is_file():
        tail = (out.parent / f"{out.stem}.log").read_bytes()[-300:].decode("utf-8", "replace").strip()
        return {"status": "timed out" if done.timed_out else f"encode failed (exit {done.exit}): {tail}"}
    res = {"status": "ok", "archive": out.stat().st_size, "packed": packed_bytes(out), "sha256": _sha256(out)}
    if unrar is not None:
        test = subprocess.run([str(unrar), "t", "-idq", "-p-", str(out)], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=timeout)
        res["unrar_exit"] = test.returncode
        if test.returncode != 0:
            res["status"] = f"unrar t exit {test.returncode}"
    return res


def run_reference(oracle, variant: Variant, cls: str, items: list, dest: Path, cache_root: Path | None,
                  unrar: Path | None, encode_missing: bool) -> dict | None:
    """The reference's archive for a class: from the oracle cache, or encoded (and cached) if allowed."""
    shas = [i.sha256 for i in items]
    enc = _cache.lookup(cache_root, oracle, variant, cls, shas, dest) if cache_root else None
    cached = enc is not None
    if enc is None:
        if not encode_missing:
            return {"status": "not cached", "cached": False}
        enc = oracle.compress(variant, [i.abs_path for i in items], dest)
        if cache_root:
            _cache.store(cache_root, oracle, variant, cls, shas, dest, enc)
    if enc.exit_code != 0 or not dest.is_file():
        return {"status": f"reference encode failed (exit {enc.exit_code})", "cached": cached}
    res = {"status": "ok", "cached": cached, "archive": dest.stat().st_size, "packed": packed_bytes(dest)}
    if res["packed"] is None:
        res["status"] = "unreadable (truncated?)"
    elif unrar is not None:
        test = subprocess.run([str(unrar), "t", "-idq", "-p-", str(dest)], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=300)
        if test.returncode != 0:
            res["status"] = f"unrar t exit {test.returncode}"
    return res


def build_oracles(args: argparse.Namespace, formats: list[str]) -> tuple[dict, dict]:
    from .harness import _build_oracle
    oracles, missing = {}, {}
    for fmt in formats:
        if fmt == "rar15":
            continue
        oracle, err = _build_oracle(fmt, args)
        if oracle is None:
            missing[fmt] = err
        else:
            oracles[fmt] = oracle
    return oracles, missing


def present_classes(root: Path, wanted: list[str] | None) -> list[str]:
    present = sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    return [c for c in present if wanted is None or c in wanted]


def run_cells(args: argparse.Namespace, rars: Path, unrar: Path | None, sets: dict, log=print) -> tuple[dict, dict]:
    formats = args.formats.split(",") if args.formats else FORMATS
    classes = present_classes(args.corpora_root, args.classes.split(",") if args.classes else None)
    if not classes:
        raise SystemExit(f"no corpus classes under {args.corpora_root}; run make seed-all")
    items = {c: enumerate_class(args.corpora_root, c) for c in classes}
    items = {c: v for c, v in items.items() if v}
    ident_prefixes = tuple(args.identity_classes.split(",")) if args.identity_classes else IDENTITY_PREFIXES
    oracles, missing = build_oracles(args, formats)
    for fmt, err in missing.items():
        log(f"  no reference for {fmt}: {err}")
    work = args.work_dir.resolve()
    shutil.rmtree(work, ignore_errors=True)
    tmax = sets["tmax"]
    cells = [Cell(fmt, solid, level, cls) for fmt in formats for cls in classes_for(fmt, list(items))
             for solid in (False, True) for level in LEVELS]
    jobs = args.jobs or max(1, len(tmax) // 2)
    cpu_pool: queue.Queue[int] = queue.Queue()
    for cpu in tmax[:jobs]:
        cpu_pool.put(cpu)

    def rars_task(cell: Cell, threads_label: str) -> dict:
        cpu = cpu_pool.get()
        try:
            cpus = str(cpu) if threads_label == "t1" else _machine.format_cpu_list(tmax)
            threads = 1 if threads_label == "t1" else len(tmax)
            out = work / "rars" / cell.id.replace("/", "_") / f"{threads_label}.rar"
            return run_rars(rars, cell, items[cell.cls], out, threads, cpus, unrar, args.timeout)
        finally:
            cpu_pool.put(cpu)

    refs: dict[tuple, object] = {}

    def ref_task(fmt: str, variant: Variant, cls: str) -> dict | None:
        dest = work / "ref" / f"{fmt}_{variant.key}_{cls}" / "out.rar"
        return run_reference(oracles[fmt], variant, cls, items[cls], dest,
                             None if args.no_oracle_cache else args.cache_root, unrar, not args.cached_refs_only)

    started = time.time()
    with ThreadPoolExecutor(jobs) as pool, ThreadPoolExecutor(args.ref_jobs) as wine_pool, \
            ThreadPoolExecutor(1) as dos_pool:
        t1 = {c: pool.submit(rars_task, c, "t1") for c in cells}
        ident = {c: pool.submit(rars_task, c, "tmax") for c in cells if c.cls.startswith(ident_prefixes)}
        for c in cells:
            rv = reference_variant(c)
            if rv is None or c.fmt not in oracles:
                continue
            key = (c.fmt, rv.key, c.cls)
            if key not in refs:
                refs[key] = (dos_pool if c.fmt in DOS_FORMATS else wine_pool).submit(ref_task, c.fmt, rv, c.cls)
        log(f"  {len(t1)} cells at t1, {len(ident)} again at tmax ({len(tmax)} threads), "
            f"{len(refs)} reference archives; {jobs} side by side")
        out: dict[str, dict] = {}
        for i, c in enumerate(cells, 1):
            r = t1[c].result()
            rv = reference_variant(c)
            ref = refs[(c.fmt, rv.key, c.cls)].result() if rv is not None and c.fmt in oracles else None
            metrics = {"input_bytes": sum(it.size for it in items[c.cls]), "threads": "t1", **r}
            if c in ident:
                x = ident[c].result()
                metrics["identity"] = {"label": "tmax", "threads": len(tmax), "status": x["status"],
                                       "archive": x.get("archive"),
                                       "same": x.get("sha256") is not None and x.get("sha256") == r.get("sha256")}
            scored = ref is not None and ref["status"] == "ok" and r.get("packed") and ref.get("packed")
            out[c.id] = {
                "ref": f"ref-{c.fmt}" if ref is not None else None, "ok": r["status"] == "ok",
                "metrics": metrics, "ref_metrics": ref,
                "ratios": {"size": store.ratio(r.get("packed"), ref["packed"]) if scored else None},
            }
            if i % 200 == 0:
                log(f"  [{time.time() - started:5.0f}s] {i}/{len(cells)} cells")
    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
    info = {"formats": formats, "classes": {c: {"files": len(v), "bytes": sum(i.size for i in v),
                                                "sha256": hashlib.sha256("".join(sorted(i.sha256 for i in v))
                                                                         .encode()).hexdigest()[:16]}
                                            for c, v in items.items()},
            "levels": LEVELS, "identity_prefixes": list(ident_prefixes), "jobs": jobs,
            "references_missing": missing,
            "references_encoded": sum(1 for f in refs.values() if f.result() and f.result().get("cached") is False
                                      and f.result()["status"] != "not cached")}
    return out, info


# --- the command ---------------------------------------------------------------------------------

def add_parsers(sub) -> None:
    from .build import add_args as add_build_args
    from .release.cli import add_history_args
    q = sub.add_parser("quick", help="the deterministic tier: sizes, unrar t, level ladder and thread identity")
    q.add_argument("--rars-bin", type=Path, default=None, help="the build under test")
    add_build_args(q)
    q.add_argument("--corpora-root", type=Path, default=Path("corpora"))
    q.add_argument("--formats", default=None, help=f"comma list (default: {','.join(FORMATS)})")
    q.add_argument("--classes", default=None, help="comma list of corpus classes (default: every one present)")
    q.add_argument("--identity-classes", default=None,
                   help=f"class-name prefixes checked at tmax too (default: {','.join(IDENTITY_PREFIXES)})")
    q.add_argument("--jobs", type=int, default=None, help="rars runs side by side (default: half of tmax)")
    q.add_argument("--ref-jobs", type=int, default=4, help="wine reference encodes side by side, when missing")
    q.add_argument("--cached-refs-only", action="store_true", help="never run wine or DOSBox; uncached cells go unscored")
    q.add_argument("--timeout", type=float, default=900.0, help="seconds per rars run")
    q.add_argument("--work-dir", type=Path, default=Path(".quick"), help="scratch archives; emptied per run")
    q.add_argument("--keep-work", action="store_true")
    q.add_argument("--unrar-bin", type=Path, default=Path("/usr/bin/unrar"))
    q.add_argument("--rar1402-bin", type=Path, default=Path("_refs/rarbins/RAR1_402.EXE"))
    q.add_argument("--rar250-bin", type=Path, default=Path("_refs/rarbins/RAR250.EXE"))
    q.add_argument("--wineprefix-root", type=Path, default=Path("_refs/wineprefixes"))
    q.add_argument("--cache-root", type=Path, default=Path("_refs/cache"), help="oracle encode cache")
    q.add_argument("--no-oracle-cache", action="store_true")
    add_history_args(q)


def run(args: argparse.Namespace) -> int:
    from .build import resolve_rars
    from .release.cli import default_label
    from .release.measure import banner, git_commit, sha256_file
    rars = resolve_rars(args)
    if rars is None:
        print("error: no rars binary; pass --rars-bin or --rars-ref", file=sys.stderr)
        return 2
    unrar = args.unrar_bin if args.unrar_bin.is_file() else None
    started = time.time()
    prof = _machine.profile(args.work_dir)
    sets = {k: _machine.parse_cpu_list(v) for k, v in prof["cpu_sets"].items()}
    load_start = _machine.load_snapshot()
    rars_info = {"version": banner([str(rars), "--version"]), "sha256": sha256_file(rars),
                 **(args.rars_build or git_commit(rars))}
    print(f"quick tier: {rars_info['version']} ({rars}), machine {prof['id']}", flush=True)
    cells, info = run_cells(args, rars, unrar, sets)
    flags = all_flags(cells)
    names = ["unrar"] + [f"ref-{f}" for f in info["formats"] if f != "rar15"]
    tools = {"rars": {"version": rars_info["version"], "sha256": rars_info["sha256"]},
             **{k: {kk: vv for kk, vv in v.items() if kk != "path"}
                for k, v in registry.describe(args, names=names).items()}}
    label = args.label or default_label(rars_info)
    record = {
        "schema": store.SCHEMA, "tier": "quick",
        "date": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(started)),
        "elapsed_s": round(time.time() - started, 1),
        "rars": {"label": label, "note": args.note,
                 **{k: v for k, v in rars_info.items() if k not in ("checkout", "path")}},
        "machine": prof, "load": {"start": load_start, "end": _machine.load_snapshot()},
        "tools": tools, "settings": info, "cells": cells,
        "checks": {"flags": flags, "counts": {k: sum(1 for f in flags if f["check"] == k)
                                              for k in ("correctness", "identity", "ladder")}},
    }
    if not args.no_history:
        print(f"history: {store.write(args.history_root, record)}")
    else:
        print(json.dumps(record["checks"]["counts"]))
    scored = [c for c in cells.values() if c["ratios"]["size"] is not None]
    print(f"{len(cells)} cells, {len(scored)} scored against a reference, "
          f"{info['references_encoded']} reference archives encoded this run, "
          f"{record['elapsed_s'] / 60:.1f} min")
    if flags:
        print(f"\n{len(flags)} check(s) broke:")
        for f in flags:
            print(f"  {f['check']:11} {f['cell']}: {f['detail']}")
        return 1
    print("every ladder, identity and correctness check held")
    return 0
