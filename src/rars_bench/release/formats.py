"""The size-and-correctness sweep over every format rars writes, each against its own era's RAR.

This is the existing `sweep` (one multi-member archive per format, level and
corpus class, written by rars and by the reference) and the existing
`scripts/summarise.py` scoring, run as one phase of the release bench with
two changes:

- rars' archives are tested by native unrar 7.20 instead of by the reference,
  so WinRAR's wine start-up is paid only for reference encodes, which the
  oracle cache replays on a re-run. The DOS formats keep their DOSBox
  cross-test as well, since that is the era's own reader.
- Reference timings are recorded but flagged: under wine or DOSBox they are
  mostly emulator start-up (about 4.4 s a cell under wine), and a cache hit
  replays an old figure. They never reach a headline. Native timing lives in
  the timed scenarios (rar 7.12, rar 3.93, unrar, rarfast).

RAR 1.5 has no reference encoder here: WinRAR 1.54 is a 16-bit GUI program
with no command line to drive, so its cells carry rars' sizes and unrar's
verdict alone.

Only cells where the reference's own archive passed its round trip are
scored (a DOS reference that times out on a large input leaves a stub).
"""
from __future__ import annotations

import argparse
import importlib.util
import statistics
import subprocess
import sys
from pathlib import Path

from ..drivers import RarsDriver, UnrarDriver
from ..formats._common import baseline_variants
from ..harness import _VARIANT_SETS, _build_oracle, _resolve_corpus_classes
from ..runner import sweep

#: (format, variant set, corpus selector, reference label, how its timing is taken)
PLAN = [
    ("rar14", "default", "small", "RAR 1.402 (DOS)", "dosbox"),
    ("rar15", "default", "all", "none", None),
    ("rar20", "default", "small", "RAR 2.50 (DOS)", "dosbox"),
    ("rar29", "default", "all", "WinRAR 2.90", "wine"),
    ("rar29", "solid", "all", "WinRAR 2.90", "wine"),
    ("rar30", "default", "all", "WinRAR 3.00", "wine"),
    ("rar30", "solid", "all", "WinRAR 3.00", "wine"),
    ("rar40", "default", "all", "WinRAR 4.20", "wine"),
    ("rar40", "solid", "all", "WinRAR 4.20", "wine"),
    ("rar50", "default", "all", "WinRAR 6.02", "wine"),
    ("rar70", "default", "all", "WinRAR 7.21b1", "wine"),
]

VARIANTS = {**{f: dict(v) for f, v in _VARIANT_SETS.items()}, "rar15": {"default": baseline_variants("rar15")}}


def _summarise_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "summarise.py"
    spec = importlib.util.spec_from_file_location("rars_bench_summarise", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(ctx, args: argparse.Namespace) -> dict:
    out_dir = ctx.run_dir / "sweep"
    out_dir.mkdir(parents=True, exist_ok=True)
    rars = RarsDriver(ctx.tools.rars)
    unrar = UnrarDriver(ctx.tools.unrar) if ctx.tools.unrar else None
    wanted = set(args.formats.split(",")) if args.formats else None
    oracle_args = argparse.Namespace(rar1402_bin=args.rar1402_bin, rar250_bin=args.rar250_bin,
                                     wineprefix_root=args.wineprefix_root)
    dbs, phases = [], []
    for fmt, vset, selector, ref_label, timing in PLAN:
        if wanted is not None and fmt not in wanted:
            continue
        classes = _resolve_corpus_classes(args.sweep_classes.split(",") if args.sweep_classes else [selector],
                                          args.corpora_root)
        if selector == "small" and args.sweep_classes:
            classes = [c for c in classes if not c.startswith(("large-", "edge-"))]
        oracle, err = (None, "no reference encoder") if fmt == "rar15" else _build_oracle(fmt, oracle_args)
        drivers = [rars] + ([oracle] if oracle else [])
        db = out_dir / f"{fmt}-{vset}.db"
        db.unlink(missing_ok=True)
        ctx.log(f"  sweep {fmt} {vset}: {len(classes)} classes, reference {oracle.version if oracle else err}")
        rows = sweep(db_path=db, corpus_root=args.corpora_root, corpus_classes=classes,
                     variants=VARIANTS[fmt][vset], drivers=drivers, work_root=ctx.scratch / "sweep-work",
                     cache_root=None if args.no_oracle_cache else args.cache_root,
                     skip_unavailable=True, cross_decoder=unrar if (timing != "dosbox") else None)
        phases.append({"format": fmt, "variants": vset, "classes": classes, "rows": rows,
                       "reference": ref_label if oracle else f"none ({err})",
                       "reference_version": oracle.version if oracle else None,
                       "reference_timing": timing if oracle else None,
                       "rars_tested_by": "unrar" if timing != "dosbox" else ref_label})
        dbs.append(db)
    return {"phases": phases, **score(ctx, dbs)}


def unrar_verdicts(ctx, work: Path) -> dict[tuple, int]:
    """unrar's exit on every archive rars wrote, keyed (variant key, class)."""
    verdicts = {}
    if ctx.tools.unrar is None:
        return verdicts
    for archive in sorted(work.glob("*/rars/*/*/out.rar")):
        done = subprocess.run([str(ctx.tools.unrar), "t", "-idq", "-p-", str(archive)],
                              capture_output=True, stdin=subprocess.DEVNULL)
        verdicts[(archive.parent.parent.name, archive.parent.name)] = done.returncode
    return verdicts


def score(ctx, dbs: list[Path]) -> dict:
    summarise = _summarise_module()
    rows = summarise.load([db for db in dbs if db.is_file()])
    verdicts = unrar_verdicts(ctx, ctx.scratch / "sweep-work")
    cells: dict[tuple, dict] = {}
    for row in rows:
        side = "rars" if row["encoder"] == "rars" else "ref"
        key = (row["format"], bool(row["solid"]), row["method"], row["corpus_class"])
        cells.setdefault(key, {})[side] = row
    table = []
    for (fmt, solid, level, cls), pair in sorted(cells.items()):
        rars, ref = pair.get("rars"), pair.get("ref")
        if rars is None:
            continue
        vkey = "-".join([fmt, "stored" if level == 0 else f"lz-{level}"] + (["solid"] if solid else []))
        if fmt == "rar14":
            vkey = "rar14-compressed"
        ref_ok = ref is not None and ref["roundtrip_status"] == "ok"
        scored = ref_ok and rars["roundtrip_status"] != "encode_failed" and rars.get("packed_bytes") \
            and ref.get("packed_bytes")
        table.append({
            "format": fmt, "solid": solid, "level": level, "class": cls, "input_bytes": rars["input_bytes"],
            "rars_status": rars["roundtrip_status"], "unrar_exit": verdicts.get((vkey, cls)),
            "ref_status": ref["roundtrip_status"] if ref else None,
            "ref_version": ref["encoder_version"] if ref else None,
            "rars_packed": rars.get("packed_bytes"), "ref_packed": ref.get("packed_bytes") if ref else None,
            "rars_archive": rars["output_bytes"], "ref_archive": ref["output_bytes"] if ref else None,
            "gap": (rars["packed_bytes"] / ref["packed_bytes"] - 1) if scored else None,
            "rars_encode_ms": round(rars["encode_ms"] or 0, 1),
            "rars_rss_kib": rars.get("encode_peak_rss"),
            "ref_encode_ms_emulated": round(ref["encode_ms"] or 0, 1) if ref else None,
        })
    levels: dict[tuple, list] = {}
    for cell in table:
        if cell["gap"] is not None:
            levels.setdefault((cell["format"], cell["solid"], cell["level"]), []).append(cell)
    rollup = []
    for (fmt, solid, level), group in sorted(levels.items()):
        large = [c for c in group if c["class"].startswith("large-")]
        small = [c for c in group if not c["class"].startswith("large-")]
        rollup.append({
            "format": fmt, "solid": solid, "level": level, "cells": len(group),
            "gap_bytes": sum(c["rars_packed"] for c in group) / sum(c["ref_packed"] for c in group) - 1,
            "median_gap": statistics.median(c["gap"] for c in group),
            "median_gap_small": statistics.median(c["gap"] for c in small) if small else None,
            "median_gap_large": statistics.median(c["gap"] for c in large) if large else None,
            "worst_class": max(group, key=lambda c: c["gap"])["class"],
            "worst_gap": max(c["gap"] for c in group),
        })
    failures = [c for c in table if c["rars_status"] != "ok" or (c["unrar_exit"] not in (0, None))]
    return {"cells": table, "rollup": rollup, "failures": failures}


def write_tsv(cells: list[dict], path: Path) -> None:
    """Every cell, worst gap first, for mining with sort/awk/a spreadsheet."""
    cols = ["gap", "format", "solid", "level", "class", "input_bytes", "rars_packed", "ref_packed",
            "rars_archive", "ref_archive", "rars_status", "unrar_exit", "ref_status", "ref_version",
            "rars_encode_ms", "rars_rss_kib", "ref_encode_ms_emulated"]
    ordered = sorted(cells, key=lambda c: (c["gap"] is None, -(c["gap"] or 0)))
    with path.open("w") as f:
        f.write("\t".join(cols) + "\n")
        for c in ordered:
            vals = []
            for col in cols:
                v = c[col]
                vals.append(f"{v:.5f}" if col == "gap" and v is not None else "" if v is None else str(v))
            f.write("\t".join(vals) + "\n")


if __name__ == "__main__":  # pragma: no cover
    sys.exit("run via `rars-bench release`")
