"""UnrarDriver — native unrar as a test-only cross-decoder.

The sweep's default cross-test sends rars' archive to the reference encoder,
which for WinRAR means starting wine for every cell. unrar 7.20 reads every
format from RAR 1.5 up natively in milliseconds, and it is the correctness
oracle the project trusts, so the release bench tests rars' archives with it.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..variants import Variant
from .base import run_timed


@dataclass
class UnrarDriver:
    binary: Path
    name: str = "unrar"
    version: str = field(default="unrar")
    cacheable: bool = False

    def available(self) -> bool:
        return self.binary.is_file() and os.access(self.binary, os.X_OK)

    def compress(self, variant, inputs, out_archive):
        raise NotImplementedError("unrar cannot write archives")

    def extract(self, variant, archive, out_dir, password=None):
        raise NotImplementedError("the sweep only tests")

    def test(self, variant: Variant, archive: Path, password: str | None = None) -> int:
        cmd = [str(self.binary), "t", "-idq", f"-p{password}" if password else "-p-", str(archive)]
        proc, _, _ = run_timed(cmd, timeout=300.0)
        return proc.returncode
