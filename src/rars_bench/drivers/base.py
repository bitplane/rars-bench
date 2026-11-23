"""Driver Protocol, result dataclasses, and timed-subprocess helper.

Linux-only: peak RSS comes from GNU time's `%M` (see `spawn`).
"""
import os
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from ..variants import Variant


@dataclass(frozen=True)
class EncodeResult:
    archive_path: Path
    archive_bytes: int
    exit_code: int
    encode_ms: float
    peak_rss_kib: int | None
    stdout: str
    stderr: str


@dataclass(frozen=True)
class DecodeResult:
    extracted_dir: Path
    member_paths: list[Path] = field(default_factory=list)
    exit_code: int = 0
    decode_ms: float = 0.0
    peak_rss_kib: int | None = None
    stdout: str = ""
    stderr: str = ""


@runtime_checkable
class Driver(Protocol):
    name: str
    version: str
    cacheable: bool             # True if encode output is deterministic for a fixed binary/version

    def available(self) -> bool: ...
    def compress(
        self, variant: Variant, inputs: list[Path], out_archive: Path
    ) -> EncodeResult: ...
    def extract(
        self,
        variant: Variant,
        archive: Path,
        out_dir: Path,
        password: str | None = None,
    ) -> DecodeResult: ...
    def test(
        self, variant: Variant, archive: Path, password: str | None = None
    ) -> int: ...


#: GNU time, used only for the peak RSS. Linux carries a process's RSS high-water
#: mark across fork and exec, so a tool started straight from this Python
#: process reports at least Python's own size (about 100 MiB here). GNU time
#: is a few MiB, and the tool it forks inherits only that.
GNU_TIME = "/usr/bin/time"


@dataclass(frozen=True)
class Spawned:
    exit: int
    wall_s: float
    user_s: float
    sys_s: float
    rss_kib: int | None
    timed_out: bool

    @property
    def cpu_s(self) -> float:
        return self.user_s + self.sys_s


def spawn(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout: float = 60.0) -> Spawned:
    """Run cmd to completion in its own session; account for it and its children only.

    Wall time is ours (GNU time's own start-up, about a millisecond, is in it);
    user and system time come from `wait4`, which counts the tool since GNU time
    waits for it; peak RSS is GNU time's `%M` for the tool.
    """
    with tempfile.NamedTemporaryFile("r", suffix=".rss") as rss_file:
        wrapped = cmd
        if os.access(GNU_TIME, os.X_OK):
            wrapped = [GNU_TIME, "-f", "%M", "-o", rss_file.name, *cmd]
        t0 = time.perf_counter()
        proc = subprocess.Popen(wrapped, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=stdout,
                                stderr=stderr, start_new_session=True)
        expired = threading.Event()

        def kill() -> None:
            expired.set()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        timer = threading.Timer(timeout, kill)
        timer.start()
        try:
            _, status, usage = os.wait4(proc.pid, 0)
        finally:
            timer.cancel()
        wall = time.perf_counter() - t0
        proc.returncode = os.waitstatus_to_exitcode(status)
        rss = None
        if wrapped is not cmd:
            lines = rss_file.read().split()
            rss = int(lines[-1]) if lines and lines[-1].isdigit() else None
    return Spawned(exit=-1 if expired.is_set() else proc.returncode, wall_s=wall, user_s=usage.ru_utime,
                   sys_s=usage.ru_stime, rss_kib=rss, timed_out=expired.is_set())


def run_timed(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[subprocess.CompletedProcess, float, int | None]:
    """Run a subprocess, return (proc, elapsed_ms, peak_rss_kib_or_None).

    Captures stdout/stderr as text. On timeout, the process is killed and a
    synthetic CompletedProcess with returncode=-1 is returned.

    The peak RSS used to be a `RUSAGE_CHILDREN` delta, which is the largest
    child reaped so far: once a big process had run, every smaller one after
    it read as None. See `spawn` for how it is taken now.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        done = spawn(cmd, cwd=cwd, env=env, stdout=out, stderr=err, timeout=timeout)
        out.seek(0)
        err.seek(0)
        proc = subprocess.CompletedProcess(cmd, done.exit, stdout=out.read().decode(errors="replace"),
                                           stderr=err.read().decode(errors="replace"))
    return proc, done.wall_s * 1000.0, (None if done.timed_out else done.rss_kib)
