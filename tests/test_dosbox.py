"""Slow tests — Rar1402Driver via DOSBox."""
import hashlib

import pytest

from rars_bench.drivers import Rar1402Driver
from rars_bench.variants import RAR14_SLICE


pytestmark = pytest.mark.slow


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_rar1402_driver_available(rar1402_bin, dosbox_or_skip):
    d = Rar1402Driver(rar1402_bin)
    assert d.available()


def test_rar1402_compresses_via_dosbox(rar1402_bin, tiny_corpus, tmp_path, dosbox_or_skip):
    d = Rar1402Driver(rar1402_bin)
    variant = RAR14_SLICE[0]
    src = tiny_corpus / "text-small" / "single.bin"
    archive = tmp_path / "single.rar"
    enc = d.compress(variant, [src], archive)
    assert enc.exit_code == 0, f"stderr={enc.stderr!r}"
    assert archive.is_file()
    assert enc.archive_bytes > 0


def test_rar1402_round_trip(rar1402_bin, tiny_corpus, tmp_path, dosbox_or_skip):
    d = Rar1402Driver(rar1402_bin)
    variant = RAR14_SLICE[0]
    src = tiny_corpus / "text-small" / "mixed.txt"
    archive = tmp_path / "mixed.rar"
    enc = d.compress(variant, [src], archive)
    assert enc.exit_code == 0, f"stderr={enc.stderr!r}"

    out_dir = tmp_path / "extracted"
    dec = d.extract(variant, archive, out_dir)
    assert dec.exit_code == 0, f"stderr={dec.stderr!r}"

    extracted_bytes = [p.read_bytes() for p in dec.member_paths]
    assert extracted_bytes
    assert _sha(src.read_bytes()) in {_sha(b) for b in extracted_bytes}
