#!/usr/bin/env python3
"""Roll results DBs up into the numbers the rars-vs-RAR page reports.

    scripts/summarise.py results/c1dbc9a2-*.db --out summary.json

A cell is one (format, level, solid, corpus class): one multi-member archive
written by each encoder. Only cells where the reference's own archive passed
its round trip are scored, because a DOS reference that times out leaves a
truncated file with a real-looking size. A rars archive the reference failed
to test is reported, never scored.

Every gap is packed bytes over the reference's: `sum(rars) / sum(ref) - 1`
over a level's cells, which large members dominate, and the median of the
per-class gaps beside it. Encode time is the same ratio over encode_ms. The
decode column in the DB is the *other* encoder testing the archive, so it
measures the reference's decoder on rars rows and is not reported.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path


def load(paths: list[Path]) -> list[dict]:
    rows = []
    for path in paths:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        for row in conn.execute("select * from runs"):
            rows.append({**dict(row), "db": path.name})
        conn.close()
    return rows


def summarise(rows: list[dict]) -> dict:
    cells: dict[tuple, dict] = defaultdict(dict)
    versions: dict[str, set] = defaultdict(set)
    for row in rows:
        side = "rars" if row["encoder"] == "rars" else "ref"
        key = (row["format"], row["method"], row["solid"], row["corpus_class"])
        cells[key][side] = row
        versions[side].add((row["format"], row["encoder_version"]))

    out = {"rars_versions": sorted({v for _, v in versions["rars"]}),
           "references": sorted({f"{f} {v}" for f, v in versions["ref"]}),
           "formats": {}}
    grouped: dict[tuple, list] = defaultdict(list)
    failures = []
    for (fmt, method, solid, corpus), pair in sorted(cells.items()):
        rars, ref = pair.get("rars"), pair.get("ref")
        if rars is None or ref is None:
            continue
        if rars["roundtrip_status"] != "ok":
            failures.append({"format": fmt, "method": method, "solid": solid,
                             "class": corpus, "status": rars["roundtrip_status"]})
        if ref["roundtrip_status"] != "ok" or rars["roundtrip_status"] == "encode_failed":
            continue
        grouped[(fmt, solid, method)].append((corpus, rars, ref))

    for (fmt, solid, method), group in sorted(grouped.items()):
        mine = sum(r["output_bytes"] for _, r, _ in group)
        theirs = sum(f["output_bytes"] for _, _, f in group)
        gaps = {c: r["output_bytes"] / f["output_bytes"] - 1 for c, r, f in group}
        my_ms = sum(r["encode_ms"] or 0 for _, r, _ in group)
        their_ms = sum(f["encode_ms"] or 0 for _, _, f in group)
        level = {
            "cells": len(group),
            "rars_bytes": mine,
            "ref_bytes": theirs,
            "gap": mine / theirs - 1,
            "median_gap": statistics.median(gaps.values()),
            "time_ratio": my_ms / their_ms if their_ms else None,
            "rars_ms": my_ms,
            "ref_ms": their_ms,
            "classes": {c: {"gap": gaps[c], "rars_bytes": r["output_bytes"],
                            "ref_bytes": f["output_bytes"], "input_bytes": r["input_bytes"],
                            "rars_ms": r["encode_ms"], "ref_ms": f["encode_ms"]}
                        for c, r, f in group},
        }
        section = out["formats"].setdefault(fmt, {}).setdefault(
            "solid" if solid else "default", {})
        section[f"m{method}"] = level
    out["rars_failures"] = failures
    return out


def show(summary: dict) -> None:
    print("rars builds:", ", ".join(summary["rars_versions"]))
    print("references:", ", ".join(summary["references"]))
    for fmt, sections in summary["formats"].items():
        for name, levels in sections.items():
            print(f"\n{fmt} {name}")
            for level, data in levels.items():
                ratio = f"{data['time_ratio']:.2f}x" if data["time_ratio"] else "-"
                print(f"  {level}: gap {data['gap']:+.2%}  median {data['median_gap']:+.2%}"
                      f"  time {ratio}  ({data['cells']} cells)")
    if summary["rars_failures"]:
        print("\nrars round-trip failures:")
        for failure in summary["rars_failures"]:
            print("  ", failure)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db", type=Path, nargs="+")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    summary = summarise(load(args.db))
    if len(summary["rars_versions"]) > 1:
        print("warning: more than one rars build across these DBs; the comparison mixes binaries")
    show(summary)
    if args.out:
        args.out.write_text(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
