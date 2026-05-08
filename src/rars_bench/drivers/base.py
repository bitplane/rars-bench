"""Driver Protocol, result dataclasses, and timed-subprocess helper.

Linux-only RSS: `resource.getrusage(RUSAGE_CHILDREN).ru_maxrss` returns KiB
on Linux (bytes on macOS). The runner is serial so the delta around a single
subprocess.run is the peak RSS of the just-reaped child.
"""
import resource
import subprocess
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


def run_timed(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> tuple[subprocess.CompletedProcess, float, int | None]:
    """Run a subprocess, return (proc, elapsed_ms, peak_rss_kib_delta_or_None).

    Captures stdout/stderr as text. On timeout, the process is killed and a
    synthetic CompletedProcess with returncode=-1 is returned.
    """
    rss_before = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        elapsed = (time.monotonic() - t0) * 1000.0
        proc = subprocess.CompletedProcess(
            cmd,
            -1,
            stdout=(e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")),
            stderr=(e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")),
        )
        return proc, elapsed, None
    elapsed = (time.monotonic() - t0) * 1000.0
    rss_after = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    delta = max(0, rss_after - rss_before)
    return proc, elapsed, (delta or None)
