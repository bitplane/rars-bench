"""The quick tier's checks, and one run end to end against a stand-in rars."""
import argparse
import sys
from pathlib import Path

from rars_bench import quick
from rars_bench.history import store

FAKE_RARS = r'''
import os, sys
args = sys.argv[1:]
threads = int(args[args.index("--threads") + 1])
level = int(args[args.index("--level") + 1])
out = next(a for a in args if a.endswith(".rar"))
size = 1000 - 10 * level
if os.environ.get("FAKE_LADDER") and level == 3:
    size += 50
body = b"x" * size
if os.environ.get("FAKE_THREADS") and threads > 1:
    body = b"y" * size
open(out, "wb").write(body)
'''


def cell(packed, status="ok", identity=None):
    m = {"packed": packed, "archive": packed + 50, "status": status}
    if identity is not None:
        m["identity"] = identity
    return {"metrics": m}


def test_ladder_flags_only_upward_steps() -> None:
    cells = {store.size_cell_id("rar50", False, lv, "text"): cell(size)
             for lv, size in zip(range(1, 6), [100, 90, 95, 95, 80])}
    cells[store.size_cell_id("rar50", True, 1, "text")] = cell(10)
    cells[store.size_cell_id("rar50", True, 2, "text")] = cell(11)
    flags = quick.ladder_breaks(cells)
    assert [f["cell"] for f in flags] == ["size/rar50/plain/m3/text", "size/rar50/solid/m2/text"]
    assert "m3 95 > m2 90" in flags[0]["detail"]


def test_identity_and_correctness_flags() -> None:
    cells = {
        "size/rar50/plain/m1/a": cell(100, identity={"label": "tmax", "same": True, "archive": 150}),
        "size/rar50/plain/m1/b": cell(100, identity={"label": "tmax", "same": False, "archive": 151}),
        "size/rar50/plain/m1/c": cell(100, status="unrar t exit 3"),
    }
    assert [f["cell"] for f in quick.identity_breaks(cells)] == ["size/rar50/plain/m1/b"]
    assert [f["detail"] for f in quick.correctness_breaks(cells)] == ["unrar t exit 3"]


def test_reference_choice() -> None:
    assert quick.reference_variant(quick.Cell("rar15", False, 3, "text")) is None
    assert quick.reference_variant(quick.Cell("rar20", True, 3, "text")) is None
    assert quick.reference_variant(quick.Cell("rar20", False, 3, "large-text")) is None
    assert quick.reference_variant(quick.Cell("rar14", False, 5, "text")).method == "compressed"
    v = quick.reference_variant(quick.Cell("rar50", True, 4, "large-text"))
    assert v.solid and v.method_int == 4
    assert quick.classes_for("rar20", ["large-text", "logs", "edge-pcm8-mono"]) == ["logs", "edge-pcm8-mono"]


def _run(tmp_path: Path, monkeypatch, **env) -> tuple[dict, dict]:
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    rars = tmp_path / "rars"
    rars.write_text(f"#!{sys.executable}\n{FAKE_RARS}")
    rars.chmod(0o755)
    corpora = tmp_path / "corpora"
    for cls in ("logs", "large-text"):
        (corpora / cls).mkdir(parents=True, exist_ok=True)
        (corpora / cls / "a.txt").write_text("hello " * 100)
    args = argparse.Namespace(formats="rar15", classes=None, corpora_root=corpora, identity_classes=None,
                              work_dir=tmp_path / "work", jobs=2, ref_jobs=1, cached_refs_only=True,
                              no_oracle_cache=True, cache_root=None, timeout=60.0, keep_work=False,
                              wineprefix_root=tmp_path, rar1402_bin=tmp_path / "x", rar250_bin=tmp_path / "x")
    sets = {"t1": [1], "t4": [1, 2], "tmax": [1, 2]}
    return quick.run_cells(args, rars, None, sets, log=lambda *_: None)


def test_run_cells_end_to_end(tmp_path: Path, monkeypatch) -> None:
    cells, info = _run(tmp_path, monkeypatch)
    assert len(cells) == 2 * 2 * 5 and info["formats"] == ["rar15"]
    c = cells["size/rar15/plain/m3/large-text"]
    assert c["ok"] and c["metrics"]["archive"] == 970 and c["metrics"]["identity"]["same"]
    assert "identity" not in cells["size/rar15/plain/m3/logs"]["metrics"]
    assert quick.all_flags(cells) == []
    assert not (tmp_path / "work").exists()


def test_run_cells_catches_ladder_and_identity(tmp_path: Path, monkeypatch) -> None:
    cells, _ = _run(tmp_path, monkeypatch, FAKE_LADDER="1", FAKE_THREADS="1")
    flags = quick.all_flags(cells)
    kinds = {(f["check"], f["cell"]) for f in flags}
    assert ("ladder", "size/rar15/plain/m3/logs") in kinds
    assert ("identity", "size/rar15/solid/m1/large-text") in kinds
    assert not any(k == "identity" and "logs" in c for k, c in kinds)
