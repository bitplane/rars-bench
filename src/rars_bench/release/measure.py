"""Run one command and account for exactly that process (see `drivers.base.spawn`)."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from ..drivers.base import Spawned as Measured
from ..drivers.base import spawn


def measure(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    log: Path | None = None,
    timeout: float = 3600.0,
    env: dict[str, str] | None = None,
) -> Measured:
    """Run `cmd` to completion, output to `log` (or nowhere), stdin closed."""
    if log is None:
        return spawn(cmd, cwd=cwd, env=env, timeout=timeout)
    with log.open("wb") as out:
        return spawn(cmd, cwd=cwd, env=env, stdout=out, stderr=subprocess.STDOUT, timeout=timeout)


def log_tail(log: Path, limit: int = 600) -> str:
    try:
        data = log.read_bytes()
    except OSError:
        return ""
    return data[-limit:].decode("utf-8", "replace").strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def banner(cmd: list[str], pattern: str = r"\S.*\d+\.\d+.*") -> str:
    """The first line of a tool's own output that names a version."""
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"unavailable ({e})"
    text = (done.stdout + done.stderr).decode("utf-8", "replace")
    for line in text.splitlines():
        if re.search(pattern, line):
            return line.strip()
    return text.strip().splitlines()[0] if text.strip() else "unknown"


def git_commit(binary: Path) -> dict:
    """The commit of the checkout a binary was built in, if it sits in one."""
    for parent in binary.resolve().parents:
        if (parent / ".git").exists():
            def git(*args: str) -> str:
                done = subprocess.run(["git", "-C", str(parent), *args], capture_output=True, text=True)
                return done.stdout.strip()
            return {"checkout": str(parent), "commit": git("rev-parse", "HEAD"),
                    "subject": git("log", "-1", "--format=%s"),
                    "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}
    return {}
