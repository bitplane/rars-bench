"""--rars-ref: clone, resolve, build once per commit, and recover from a broken checkout. No real cargo."""
import subprocess
import sys
from pathlib import Path

import pytest

from rars_bench import build

FAKE_CARGO = r'''
import os, pathlib, sys
out = pathlib.Path(os.environ["CARGO_TARGET_DIR"]) / "release" / "rars"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("#!/bin/sh\necho rars " + pathlib.Path("VERSION").read_text().strip() + "\n")
out.chmod(0o755)
with open(os.environ["FAKE_CARGO_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
'''


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    repo = tmp_path / "upstream"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "master")
    git(repo, "config", "user.email", "bench@example.invalid")
    git(repo, "config", "user.name", "bench")
    for version in ("0.13.0", "0.13.1"):
        (repo / "VERSION").write_text(version + "\n")
        git(repo, "add", "VERSION")
        git(repo, "commit", "-q", "-m", version)
        git(repo, "tag", f"v{version}")
    return repo


@pytest.fixture
def cargo(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "bin" / "cargo"
    path.parent.mkdir()
    path.write_text(f"#!{sys.executable}\n{FAKE_CARGO}")
    path.chmod(0o755)
    monkeypatch.setenv("FAKE_CARGO_LOG", str(tmp_path / "cargo.log"))
    return path


def test_builds_a_tag_once(tmp_path: Path, upstream: Path, cargo: Path) -> None:
    cache = tmp_path / "builds"
    binary, info = build.build("v0.13.0", cache, str(upstream), str(cargo), log=lambda *_: None)
    assert subprocess.run([binary], capture_output=True, text=True).stdout.strip() == "rars 0.13.0"
    assert info["tag"] == "v0.13.0" and info["commit"] == git(upstream, "rev-parse", "v0.13.0")
    assert info["build"] == "cargo build --release --locked -p rars-cli --bin rars"
    again, _ = build.build("v0.13.0", cache, str(upstream), str(cargo), log=lambda *_: None)
    assert again == binary
    assert len((tmp_path / "cargo.log").read_text().splitlines()) == 1


def test_resolves_branches_and_commits(tmp_path: Path, upstream: Path, cargo: Path) -> None:
    cache = tmp_path / "builds"
    head = git(upstream, "rev-parse", "HEAD")
    binary, info = build.build("master", cache, str(upstream), str(cargo), log=lambda *_: None)
    assert info["commit"] == head and binary.name == f"rars-{head[:12]}"
    _, info = build.build(head[:10], cache, str(upstream), str(cargo), log=lambda *_: None)
    assert info["commit"] == head
    with pytest.raises(SystemExit):
        build.build("no-such-ref", cache, str(upstream), str(cargo), log=lambda *_: None)


def test_reclones_a_broken_worktree(tmp_path: Path, upstream: Path, cargo: Path) -> None:
    src = tmp_path / "builds" / "src"
    src.mkdir(parents=True)
    # What a worktree whose main repository was deleted looks like.
    (src / ".git").write_text(f"gitdir: {tmp_path}/gone/.git/worktrees/src\n")
    (src / "stray").write_text("x")
    assert not build.healthy(src)
    notes = []
    binary, _ = build.build("v0.13.1", tmp_path / "builds", str(upstream), str(cargo), log=notes.append)
    assert build.healthy(src) and not (src / "stray").exists()
    assert any("broken" in n for n in notes)
    assert subprocess.run([binary], capture_output=True, text=True).stdout.strip() == "rars 0.13.1"
