#!/usr/bin/env python3
"""Decode time of rars against unrar, on the archives the reference encoders wrote.

    scripts/decode_timing.py --rars _refs/rars-<sha> --unrar unrar \
        --work results/work --levels 3,5 --out decode.json

Both decoders test the same archive, the one the era's own RAR wrote during
the sweep, so neither is reading its own encoder's habits. `test` decodes and
verifies every member without writing files, which keeps the disk out of it.
The two alternate within each round and the minimum over the rounds is kept.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from pathlib import Path

# The sweep's work tree names each cell <run>/<encoder>/<format>-<method>[-solid...]/<class>/out.rar.
CELL = re.compile(r"^(rar\d\d)-lz-(\d)(-solid)?$")


def run(cmd: list[str]) -> tuple[float, int]:
    start = time.perf_counter()
    done = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - start, done.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rars", required=True)
    parser.add_argument("--unrar", default="unrar")
    parser.add_argument("--work", type=Path, default=Path("results/work"))
    parser.add_argument("--levels", default="3,5")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    levels = {int(level) for level in args.levels.split(",")}
    archives = {}
    for archive in sorted(args.work.glob("*/rar/*/*/out.rar")):
        match = CELL.match(archive.parent.parent.name)
        if not match or int(match[2]) not in levels:
            continue
        key = (match[1], int(match[2]), bool(match[3]), archive.parent.name)
        archives.setdefault(key, archive)  # one per cell, if a format ran twice
    results = []
    for (fmt, level, solid, cls), archive in sorted(archives.items()):
        best = {"rars": float("inf"), "unrar": float("inf")}
        status = {}
        for round_ in range(args.rounds):
            order = ["rars", "unrar"] if round_ % 2 == 0 else ["unrar", "rars"]
            for decoder in order:
                cmd = ([args.rars, "test", str(archive)] if decoder == "rars"
                       else [args.unrar, "t", "-idq", str(archive)])
                seconds, code = run(cmd)
                best[decoder] = min(best[decoder], seconds)
                status[decoder] = code
        results.append({"format": fmt, "level": level, "solid": solid, "class": cls,
                        "archive_bytes": archive.stat().st_size,
                        "rars_s": best["rars"], "unrar_s": best["unrar"],
                        "rars_exit": status["rars"], "unrar_exit": status["unrar"]})
        print(f"{fmt} m{level}{' solid' if solid else ''} {cls:22} rars {best['rars']:7.3f}s"
              f"  unrar {best['unrar']:7.3f}s  {best['rars'] / best['unrar']:6.2f}x"
              f"  exits {status['rars']}/{status['unrar']}", flush=True)
    args.out.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
