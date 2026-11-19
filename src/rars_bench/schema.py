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
    rars_internal    TEXT,                     -- JSON sidecar from `rars a --report-json`, or NULL
    packed_bytes     INTEGER                   -- sum of the members' packed sizes; NULL if unreadable
);

CREATE INDEX IF NOT EXISTS idx_runs_variant
    ON runs(format, method, filter, solid, encrypted, dict_log, corpus_path);

CREATE INDEX IF NOT EXISTS idx_runs_run_id
    ON runs(run_id);
"""


#: Columns added after the first DBs were written, with their types. init_db
#: adds any that an older DB lacks, so its rows read as NULL rather than the
#: DB being refused.
ADDED_COLUMNS = {"packed_bytes": "INTEGER"}


def init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    migrate(conn)
    conn.commit()
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    present = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    for name, kind in ADDED_COLUMNS.items():
        if name not in present:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {kind}")


def connect_read(path: Path) -> sqlite3.Connection:
    """Open a results DB for reading, with every current column present.

    A DB written before a column was added gets a temporary `runs` view that
    supplies it as NULL. The view lives in the connection's temp schema, which
    sqlite searches before the file's, so the file itself is not changed.
    """
    conn = sqlite3.connect(path)
    present = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    missing = [name for name in ADDED_COLUMNS if name not in present]
    if present and missing:
        extra = ", ".join(f"NULL AS {name}" for name in missing)
        conn.execute(f"CREATE TEMP VIEW runs AS SELECT *, {extra} FROM main.runs")
    return conn
