"""Testing a seeded sample of real legacy archives against unrar's verdict.

The corpus is read in place and never written: every command is a `test`.
The sample is drawn from the manifest's `ok` rows (unrar 7.20 tested them
clean) at or under a size cap, with a fixed seed, and kept in the input cache
so a later run tests the same archives. Each archive is tested by every tool
in turn, the order rotating per round, and the minimum per archive is kept,
so the totals are sums of per-archive minimums.

Agreement is what the scenario is for: rars should pass every archive unrar
passes. Throughput on a sample this size is mostly process start-up and small
files, which is what reading many smallish legacy archives costs in practice.
"""
from __future__ import annotations

import json
import random
import statistics
from collections import defaultdict
from pathlib import Path

from .measure import log_tail, measure

SIGS = [(b"RE~^", "rar1.3"), (b"Rar!\x1a\x07\x01\x00", "rar5"), (b"Rar!\x1a\x07\x00", "rar1.5-4")]


def family(path: Path) -> str:
    """Archive family by signature: at offset 0, or inside an SFX stub."""
    try:
        with path.open("rb") as f:
            head = f.read(1 << 20)
    except OSError:
        return "unreadable"
    for sig, name in SIGS:
        if head.startswith(sig):
            return name
    hits = [(head.find(sig), name) for sig, name in SIGS if head.find(sig) > 0]
    return f"sfx-{min(hits)[1]}" if hits else "other"


def sample(data_root: Path, manifest: Path, count: int, max_bytes: int, seed: int, cache: Path) -> list[dict]:
    key = cache / f"discmaster-sample-{count}-{max_bytes}-{seed}.json"
    if key.is_file():
        return json.loads(key.read_text())
    rows = []
    with manifest.open() as f:
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 4 or cols[2] != "ok" or cols[3] != "0":
                continue
            size = int(cols[1])
            if size <= max_bytes:
                rows.append((cols[0], size))
    rows.sort()
    chosen = sorted(random.Random(seed).sample(rows, min(count, len(rows))))
    picked = [{"path": rel, "size": size, "family": family(data_root / rel)} for rel, size in chosen]
    cache.mkdir(parents=True, exist_ok=True)
    key.write_text(json.dumps(picked))
    return picked


def commands(tools, archive: Path) -> dict[str, list[str]]:
    cmds = {"rars": [str(tools.rars), "--progress", "never", "test", str(archive)]}
    for name in ("unrar", "rarfast"):
        binary = getattr(tools, name)
        if binary is not None:
            cmds[name] = [str(binary), "t", "-idq", "-p-", str(archive)]
    return cmds


def run(ctx, data_root: Path, manifest: Path) -> dict:
    scale = ctx.scale
    picked = sample(data_root, manifest, scale["dm_count"], scale["dm_max_bytes"], scale["dm_seed"],
                    ctx.inputs.root)
    log = ctx.scratch / "dm.log"
    per_archive = []
    for n, row in enumerate(picked):
        archive = data_root / row["path"]
        cmds = commands(ctx.tools, archive)
        names = list(cmds)
        seen: dict[str, list] = {name: [] for name in names}
        for round_ in range(scale["dm_rounds"]):
            shift = (n + round_) % len(names)
            for name in names[shift:] + names[:shift]:
                m = measure(cmds[name], cwd=ctx.scratch, log=log, timeout=ctx.dm_timeout)
                seen[name].append((m, log_tail(log, 300) if m.exit != 0 else ""))
        best = {}
        for name, got in seen.items():
            # A failure in any round is the verdict; the time is the fastest clean round.
            failed = [(m, tail) for m, tail in got if m.exit != 0]
            clean = [m for m, _ in got if m.exit == 0] or [m for m, _ in got]
            entry = {"wall_s": min(m.wall_s for m in clean), "rss_kib": max(m.rss_kib or 0 for m, _ in got),
                     "exit": failed[0][0].exit if failed else 0,
                     "timed_out": any(m.timed_out for m, _ in got)}
            if failed:
                entry["tail"] = failed[0][1]
            best[name] = entry
        per_archive.append({**row, "tools": best})
        if (n + 1) % 250 == 0:
            ctx.log(f"    discmaster {n + 1}/{len(picked)}")
    return summarise(per_archive, list(commands(ctx.tools, Path("x"))))


def summarise(per_archive: list[dict], tools: list[str]) -> dict:
    by_family: dict[str, list[dict]] = defaultdict(list)
    for row in per_archive:
        by_family[row["family"]].append(row)
    by_family["all"] = per_archive

    def stats(rows: list[dict]) -> dict:
        out = {"archives": len(rows), "bytes": sum(r["size"] for r in rows)}
        for tool in tools:
            got = [r["tools"][tool] for r in rows if tool in r["tools"]]
            walls = [g["wall_s"] for g in got]
            out[tool] = {
                "pass": sum(1 for g in got if g["exit"] == 0),
                "wall_total_s": round(sum(walls), 3),
                "wall_median_s": round(statistics.median(walls), 4) if walls else None,
                "rss_max_kib": max((g["rss_kib"] for g in got), default=None),
                "rss_median_kib": statistics.median([g["rss_kib"] for g in got]) if got else None,
                "mb_per_s": round(out["bytes"] / sum(walls) / 1e6, 2) if walls and sum(walls) else None,
            }
        return out

    disagreements = [
        {"path": r["path"], "family": r["family"], "size": r["size"],
         **{f"{t}_exit": r["tools"][t]["exit"] for t in tools if t in r["tools"]},
         "rars_tail": r["tools"]["rars"].get("tail", "")}
        for r in per_archive
        if r["tools"].get("unrar", {}).get("exit") == 0 and r["tools"]["rars"]["exit"] != 0
    ]
    unrar_now_fails = [r["path"] for r in per_archive if r["tools"].get("unrar", {}).get("exit", 0) != 0]
    return {"families": {f: stats(rows) for f, rows in sorted(by_family.items())},
            "rars_fails_where_unrar_passes": disagreements,
            "unrar_fails_now": unrar_now_fails,
            "archives": per_archive}
