"""Pytest fixtures for rars-bench."""
import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def rars_bin() -> Path:
    """Locate the rars binary; skip the test if not built."""
    explicit = os.environ.get("RARS_BIN")
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    candidates.extend(
        [
            Path(__file__).resolve().parent.parent.parent / "rars" / "target" / "release" / "rars",
            Path(__file__).resolve().parent.parent.parent / "rars" / "target" / "debug" / "rars",
        ]
    )
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c.resolve()
    pytest.skip("rars binary not built; run `cargo build --release` in ../rars/")


@pytest.fixture
def rar1402_bin() -> Path:
    """Locate the mirrored RAR1_402.EXE; skip if not copied."""
    here = Path(__file__).resolve().parent.parent
    p = here / "_refs" / "rarbins" / "RAR1_402.EXE"
    if not p.is_file():
        pytest.skip(
            "RAR1_402.EXE not mirrored; run scripts/copy-refbins.sh"
        )
    return p


@pytest.fixture
def dosbox_or_skip() -> str:
    if shutil.which("dosbox-x") is None:
        pytest.skip("dosbox-x not installed")
    return "dosbox-x"


@pytest.fixture
def tiny_corpus(tmp_path: Path) -> Path:
    """Five-file corpus deterministic enough for round-trip assertions."""
    base = tmp_path / "corpora" / "text-small"
    base.mkdir(parents=True)
    (base / "empty.bin").write_bytes(b"")
    (base / "single.bin").write_bytes(b"A")
    (base / "zeros.bin").write_bytes(b"\x00" * 4096)
    (base / "repeats.bin").write_bytes((b"abc" * 342)[:1024])
    (base / "mixed.txt").write_text(
        "The quick brown fox jumps over the lazy dog.\n"
        "Pack my box with five dozen liquor jugs.\n"
        "Sphinx of black quartz, judge my vow.\n"
    )
    return tmp_path / "corpora"
