"""Fast tests — RarsDriver self-roundtrip, no DOSBox."""
import hashlib
from pathlib import Path

from rars_bench.drivers import RarsDriver
from rars_bench.variants import RAR14_SLICE


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_rars_driver_reports_version(rars_bin):
    d = RarsDriver(rars_bin)
    assert d.available()
    assert d.version.startswith("rars@")
    assert len(d.version) > len("rars@")


def test_rars_compresses_and_self_decodes(rars_bin, tiny_corpus, tmp_path):
    d = RarsDriver(rars_bin)
    variant = RAR14_SLICE[0]
    src_dir = tiny_corpus / "text-small"
    for src in sorted(src_dir.iterdir()):
        if not src.is_file() or src.stat().st_size == 0:
            # rar 1.4 fails on empty files via DOSBox; rars itself is fine, but
            # to stay aligned with the cross-decode test we skip empties here
            # too.
            continue
        archive = tmp_path / f"{src.stem}.rar"
        enc = d.compress(variant, [src], archive)
        assert enc.exit_code == 0, enc.stderr
        assert archive.is_file()
        assert enc.archive_bytes > 0

        out_dir = tmp_path / f"{src.stem}-out"
        dec = d.extract(variant, archive, out_dir)
        assert dec.exit_code == 0, dec.stderr

        extracted = list(out_dir.rglob("*"))
        files = [p for p in extracted if p.is_file()]
        assert files, f"no files extracted from {archive}"
        assert any(_sha(p) == _sha(src) for p in files)


def test_rars_test_returns_zero_on_valid_archive(rars_bin, tiny_corpus, tmp_path):
    d = RarsDriver(rars_bin)
    variant = RAR14_SLICE[0]
    src = tiny_corpus / "text-small" / "mixed.txt"
    archive = tmp_path / "valid.rar"
    enc = d.compress(variant, [src], archive)
    assert enc.exit_code == 0
    assert d.test(variant, archive) == 0
