"""RarsDriver — wraps the `rars` Rust CLI."""
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..variants import Variant
from .base import DecodeResult, EncodeResult, run_timed


@dataclass
class RarsDriver:
    binary: Path
    name: str = "rars"
    version: str = field(init=False)
    cacheable: bool = False     # rars is the SUT — re-run every sweep

    def __post_init__(self) -> None:
        if self.binary.is_file():
            sha16 = _sha16(self.binary)
            self.version = f"rars@{sha16}"
        else:
            self.version = "rars@missing"

    def available(self) -> bool:
        return self.binary.is_file() and os.access(self.binary, os.X_OK)

    def compress(
        self,
        variant: Variant,
        inputs: list[Path],
        out_archive: Path,
    ) -> EncodeResult:
        out_archive.parent.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.binary), "a", "--format", variant.format]
        if variant.method == "stored":
            cmd.append("--store")
        elif variant.method == "ppmd":
            cmd.append("--ppmd")
        elif variant.method.startswith("lz-") and variant.method != "lz-default":
            cmd.extend(["--level", str(variant.method_int)])
        # 'compressed' / 'lz-default' → no flag, rars writer picks its
        # internal default (currently equivalent to -m3).
        if variant.solid:
            cmd.append("--solid")
        if variant.password:
            cmd.extend(["--password", variant.password])
        if variant.volume_size is not None:
            cmd.extend(["--volume-size", str(variant.volume_size)])
        cmd.append(str(out_archive))
        cmd.extend(str(p) for p in inputs)

        proc, ms, rss = run_timed(cmd)
        archive_bytes = out_archive.stat().st_size if out_archive.exists() else 0
        return EncodeResult(
            archive_path=out_archive,
            archive_bytes=archive_bytes,
            exit_code=proc.returncode,
            encode_ms=ms,
            peak_rss_kib=rss,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def extract(
        self,
        variant: Variant,
        archive: Path,
        out_dir: Path,
        password: str | None = None,
    ) -> DecodeResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = [str(self.binary), "x"]
        if password:
            cmd.extend(["--password", password])
        cmd.extend([str(archive), str(out_dir)])

        proc, ms, rss = run_timed(cmd)
        members = sorted(p for p in out_dir.rglob("*") if p.is_file())
        return DecodeResult(
            extracted_dir=out_dir,
            member_paths=members,
            exit_code=proc.returncode,
            decode_ms=ms,
            peak_rss_kib=rss,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def test(
        self,
        variant: Variant,
        archive: Path,
        password: str | None = None,
    ) -> int:
        cmd = [str(self.binary), "test"]
        if password:
            cmd.extend(["--password", password])
        cmd.append(str(archive))
        proc, _, _ = run_timed(cmd)
        return proc.returncode


def _sha16(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()[:16]
