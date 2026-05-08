"""End-to-end sweep test — slow, requires DOSBox.

Runner now archives whole corpus_class dirs as one multi-member archive each,
so row count is (variants × corpus_classes × encoders), not (× files).
"""
import sqlite3
from pathlib import Path

import pytest

from rars_bench.drivers import Rar1402Driver, RarsDriver
from rars_bench.runner import sweep
from rars_bench.variants import RAR14_SLICE


pytestmark = pytest.mark.slow


def test_sweep_writes_one_row_per_encoder(
    rars_bin, rar1402_bin, dosbox_or_skip, tiny_corpus, tmp_path
):
    db_path = tmp_path / "results.db"
    work_root = tmp_path / "work"

    rars_drv = RarsDriver(rars_bin)
    rar_drv = Rar1402Driver(rar1402_bin)

    # Drop empty.bin — RAR 1.402 fails on zero-byte members and the test isn't
    # validating that behaviour here.
    (tiny_corpus / "text-small" / "empty.bin").unlink()

    rows = sweep(
        db_path=db_path,
        corpus_root=tiny_corpus,
        corpus_classes=["text-small"],
        variants=RAR14_SLICE,
        drivers=[rars_drv, rar_drv],
        work_root=work_root,
    )
    # One archive per (corpus_class × encoder) for the single-variant slice.
    assert rows == 2

    conn = sqlite3.connect(db_path)
    encoders = sorted(r[0] for r in conn.execute("SELECT encoder FROM runs").fetchall())
    statuses = sorted(
        r[0] for r in conn.execute("SELECT roundtrip_status FROM runs").fetchall()
    )
    output_bytes = [
        r[0] for r in conn.execute("SELECT output_bytes FROM runs").fetchall()
    ]
    input_bytes = [
        r[0] for r in conn.execute("SELECT input_bytes FROM runs").fetchall()
    ]
    conn.close()

    assert encoders == ["rar", "rars"]
    assert statuses == ["ok", "ok"]
    assert all(b is not None and b > 0 for b in output_bytes)
    # input_bytes is the SUM across the multi-member corpus, not a single file.
    assert all(b > 1 for b in input_bytes)


def test_sweep_skips_unavailable_when_flagged(rars_bin, tiny_corpus, tmp_path):
    db_path = tmp_path / "skip.db"
    work_root = tmp_path / "work"
    rars_drv = RarsDriver(rars_bin)
    bogus_rar = Rar1402Driver(Path("/nonexistent/RAR1_402.EXE"))

    (tiny_corpus / "text-small" / "empty.bin").unlink()

    rows = sweep(
        db_path=db_path,
        corpus_root=tiny_corpus,
        corpus_classes=["text-small"],
        variants=RAR14_SLICE,
        drivers=[rars_drv, bogus_rar],
        work_root=work_root,
        skip_unavailable=True,
    )
    assert rows == 1     # rars-only, self-decode
