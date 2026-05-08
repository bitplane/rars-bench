"""Triage — read a results DB and print rows ranked by anomaly.

Three modes for the slice:
  all       — every row, ordered by (corpus_path, encoder)
  ratio-gap — self-join on the variant key, biggest |rars - rar| first
  failures  — rows whose roundtrip_status != 'ok'
"""
import sqlite3
from pathlib import Path


_ALL_SQL = """
SELECT corpus_path, encoder, encoder_version, format, method,
       input_bytes, output_bytes, encode_ms, decode_ms, roundtrip_status
FROM runs
ORDER BY corpus_path, encoder
"""

_RATIO_GAP_SQL = """
SELECT
    a.corpus_path,
    a.format, a.method,
    a.output_bytes AS rars_bytes,
    b.output_bytes AS rar_bytes,
    (a.output_bytes - b.output_bytes) AS gap
FROM runs a
JOIN runs b
  ON a.format = b.format
 AND a.method IS b.method
 AND a.solid IS b.solid
 AND a.encrypted IS b.encrypted
 AND a.dict_log IS b.dict_log
 AND a.corpus_path = b.corpus_path
 AND a.run_id = b.run_id
WHERE a.encoder = 'rars' AND b.encoder = 'rar'
ORDER BY ABS(a.output_bytes - b.output_bytes) DESC
LIMIT ?
"""

_FAILURES_SQL = """
SELECT ts, encoder, encoder_version, format, method,
       corpus_path, output_bytes, roundtrip_status
FROM runs
WHERE roundtrip_status IS NOT NULL AND roundtrip_status != 'ok'
ORDER BY ts DESC
LIMIT ?
"""


def run(db_path: Path, by: str, top: int) -> int:
    if not db_path.is_file():
        print(f"db not found: {db_path}")
        return 2
    conn = sqlite3.connect(db_path)
    try:
        if by == "all":
            rows = conn.execute(_ALL_SQL).fetchall()
            _print_rows(
                rows,
                ["corpus_path", "encoder", "version", "fmt", "m", "in", "out", "enc_ms", "dec_ms", "status"],
            )
        elif by == "ratio-gap":
            rows = conn.execute(_RATIO_GAP_SQL, (top,)).fetchall()
            if not rows:
                print("(no rars/rar pairs to compare)")
                return 0
            _print_rows(
                rows,
                ["corpus_path", "fmt", "m", "rars_bytes", "rar_bytes", "gap"],
            )
        elif by == "failures":
            rows = conn.execute(_FAILURES_SQL, (top,)).fetchall()
            if not rows:
                print("(no failures)")
                return 0
            _print_rows(
                rows,
                ["ts", "encoder", "version", "fmt", "m", "corpus_path", "out", "status"],
            )
        else:
            print(f"unknown --by: {by}")
            return 2
        return 0
    finally:
        conn.close()


def _print_rows(rows: list[tuple], headers: list[str]) -> None:
    if not rows:
        print("(no rows)")
        return
    cols = list(zip(*([tuple(headers)] + [tuple(_fmt(v) for v in r) for r in rows])))
    widths = [max(len(c) for c in col) for col in cols]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(_fmt(v).ljust(w) for v, w in zip(r, widths)))


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)
