"""Sqlite result schema. One row per (encoder x variant x corpus_path) compress operation.

Triage queries join rars and rar rows on the variant key:
    (format, method, filter, solid, encrypted, dict_log, corpus_path).
"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT    NOT NULL,
    ts               TEXT    NOT NULL,         -- ISO 8601
    format           TEXT    NOT NULL,         -- 'rar13' | 'rar15_40' | 'rar29' | 'rar50' | 'rar70'
    method           INTEGER,                  -- 0..5
    filter           TEXT,                     -- 'none' | 'auto' | 'x86' | 'delta' | ...
    solid            INTEGER,                  -- 0 / 1
    encrypted        INTEGER,                  -- 0 / 1
    dict_log         INTEGER,                  -- log2 dict size, or NULL
    corpus_class     TEXT    NOT NULL,         -- subdir name under corpora/
    corpus_path      TEXT    NOT NULL,         -- relative to corpora/
    input_bytes      INTEGER NOT NULL,
    encoder          TEXT    NOT NULL,         -- 'rars' | 'rar'
    encoder_version  TEXT    NOT NULL,
    output_bytes     INTEGER,
    encode_ms        REAL,
    encode_peak_rss  INTEGER,
    decode_ms        REAL,
    decode_peak_rss  INTEGER,
    roundtrip_status TEXT,                     -- 'ok' | 'decode_failed' | 'wrong_bytes' | 'encode_failed'
    rars_internal    TEXT                      -- JSON sidecar from `rars a --report-json`, or NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_variant
    ON runs(format, method, filter, solid, encrypted, dict_log, corpus_path);

CREATE INDEX IF NOT EXISTS idx_runs_run_id
    ON runs(run_id);
"""


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
