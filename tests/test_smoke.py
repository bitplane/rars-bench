import sqlite3
from pathlib import Path

import rars_bench
from rars_bench.schema import init_db


def test_package_has_version():
    assert rars_bench.__version__


def test_init_db_creates_schema(tmp_path: Path):
    db = tmp_path / "results.db"
    conn = init_db(db)
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'")
        assert cur.fetchone() == ("runs",)
    finally:
        conn.close()


def test_init_db_is_idempotent(tmp_path: Path):
    db = tmp_path / "results.db"
    init_db(db).close()
    init_db(db).close()
    conn = sqlite3.connect(db)
    try:
        cur = conn.execute("SELECT count(*) FROM runs")
        assert cur.fetchone() == (0,)
    finally:
        conn.close()
