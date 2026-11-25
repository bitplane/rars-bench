"""The tool registry: lookups, version reading and change flags, with stand-in binaries."""
import argparse
from pathlib import Path

from rars_bench import registry
from rars_bench.history import store
from test_history import _sweep_scenario
from test_release import _result


def fake_tool(path: Path, banner: str) -> Path:
    path.write_text(f"#!/bin/sh\necho '{banner}'\n")
    path.chmod(0o755)
    return path


def test_reference_for_cells() -> None:
    assert registry.reference_for("create-rar5/text/m3/t1/rars") == "rar"
    assert registry.reference_for("create-rar4/text/m3/tmax/rars") == "rar393"
    assert registry.reference_for("volumes-rars/create/tmax/rars") == "rar"
    assert registry.reference_for("volumes-rars/test/tmax/rars") == "unrar"
    assert registry.reference_for("size/rar50/solid/m5/large-text") == "ref-rar50"
    assert registry.reference_for("nothing/here") is None


def test_release_cells_name_registered_tools() -> None:
    result = _result("full", 2.0, 1100)
    result["scenarios"].append(_sweep_scenario())
    rec = store.from_release(result, label="x", machine={"id": "m"}, load=None)
    for cid, cell in rec["cells"].items():
        if cell["ref"]:
            assert registry.reference_for(cid) == cell["ref"], cid


def test_describe_reads_versions_and_hashes(tmp_path: Path) -> None:
    args = argparse.Namespace(unrar_bin=fake_tool(tmp_path / "unrar", "UNRAR 7.20 freeware      Copyright"),
                              rar_bin=fake_tool(tmp_path / "rar", "RAR 7.12   Copyright (c) 1993-2025"),
                              rar393_bin=tmp_path / "missing", wineprefix_root=tmp_path / "none",
                              rar1402_bin=tmp_path / "missing", rar250_bin=tmp_path / "missing")
    tools = registry.describe(args, names=["unrar", "rar", "rar393", "ref-rar50"])
    assert tools["unrar"]["version"] == "UNRAR 7.20 freeware"
    assert tools["rar"]["version"] == "RAR 7.12" and len(tools["rar"]["sha256"]) == 64
    assert tools["rar393"]["version"] == "not found"
    assert tools["ref-rar50"]["version"] == "not found"


def test_tool_changes_flags_version_and_hash() -> None:
    old = {"unrar": {"version": "UNRAR 7.20", "sha256": "a" * 64}, "rar": {"version": "RAR 7.12", "sha256": "b" * 64},
           "gone": {"version": "1"}}
    new = {"unrar": {"version": "UNRAR 7.21", "sha256": "c" * 64}, "rar": {"version": "RAR 7.12", "sha256": "d" * 64}}
    lines = registry.tool_changes(old, new)
    assert lines[0].startswith("rar: same version, sha256") and "UNRAR 7.20 -> UNRAR 7.21" in lines[1]
    assert registry.tool_changes(old, old) == []
