#!/usr/bin/env python3
"""Encode time of rars against native RAR 7.12, without wine in the measurement.

    scripts/timing.py --rars _refs/rars-<sha> --rar _refs/rarbins/rar712-linux-x64 \
        --out timing.json

The sweep's reference timings include starting wine, about four seconds a
cell, which swamps the work itself. This times the native Linux RAR 7.12
instead, on the same corpus classes, writing RAR 5 at the same level with
each encoder's defaults otherwise. The two binaries alternate within each
round and the minimum over the rounds is kept, so ambient load and a cold
cache hit both alike.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path


def files(corpus: Path, cls: str) -> list[str]:
    return sorted(str(p) for p in (corpus / cls).iterdir() if p.is_file())


def run(cmd: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rars", required=True)
    parser.add_argument("--rar", required=True)
    parser.add_argument("--corpus", type=Path, default=Path("corpora"))
    parser.add_argument("--levels", default="1,3,5")
    parser.add_argument("--format", default="rar50", help="the rars --format to write")
    parser.add_argument("--rar-args", default="-ma5",
                        help="the reference's format switches; RAR 3.93 takes none")
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    classes = sorted(p.name for p in args.corpus.iterdir() if p.is_dir() and any(p.iterdir()))
    results = []
    with tempfile.TemporaryDirectory(dir=".") as scratch:
        for level in [int(level) for level in args.levels.split(",")]:
            for cls in classes:
                inputs = files(args.corpus, cls)
                best = {"rars": float("inf"), "rar": float("inf")}
                sizes = {}
                for round_ in range(args.rounds):
                    # Alternate which goes first, so neither always gets the warm cache.
                    order = ["rars", "rar"] if round_ % 2 == 0 else ["rar", "rars"]
                    for encoder in order:
                        out = Path(scratch) / f"{encoder}.rar"
                        out.unlink(missing_ok=True)
                        if encoder == "rars":
                            cmd = [args.rars, "add", "--format", args.format, "--level", str(level),
                                   str(out), *inputs]
                        else:
                            cmd = [args.rar, "a", *args.rar_args.split(), f"-m{level}", "-ep", "-idq",
                                   str(out), *inputs]
                        best[encoder] = min(best[encoder], run(cmd))
                        sizes[encoder] = out.stat().st_size
                results.append({"level": level, "class": cls,
                                "input_bytes": sum(Path(p).stat().st_size for p in inputs),
                                "rars_s": best["rars"], "rar_s": best["rar"],
                                "rars_bytes": sizes["rars"], "rar_bytes": sizes["rar"]})
                print(f"m{level} {cls:24} rars {best['rars']:7.3f}s  rar {best['rar']:7.3f}s"
                      f"  {best['rars'] / best['rar']:6.2f}x", flush=True)
    args.out.write_text(json.dumps(results, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
