"""WineRarDriver — drives a Windows Rar.exe inside a wineprefix.

Construction takes a `prefix_dir` and a `relative_exe_path` (path to
`Rar.exe` inside the prefix). The driver invokes `wine` with WINEPREFIX
set to that prefix; wine's drive_c maps to host paths, so we run rar
against host-absolute paths via the `Z:` mount that wine auto-creates
(Z: == /).

Linux long filenames pass through wine without 8.3 truncation, so unlike
DOSBox there's no collision risk on multi-member archives. We still pass
8.3-friendly names to keep apples-to-apples ratio comparison with the
DOS-era oracle when both run side by side, but it's not required for
correctness.
"""
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..variants import Variant
from .base import DecodeResult, EncodeResult, run_timed


@dataclass
class WineRarDriver:
    prefix_dir: Path
    relative_exe: Path                  # e.g. Path("drive_c/Program Files (x86)/WinRAR/Rar.exe")
    name: str = "rar"
    version: str = "unknown"            # set by caller (e.g. '2.90', '3.00', '4.20', '6.02')
    cacheable: bool = True

    def available(self) -> bool:
        if shutil.which("wine") is None:
            return False
        return self.exe_path.is_file()

    @property
    def exe_path(self) -> Path:
        return self.prefix_dir / self.relative_exe

    def _env(self) -> dict[str, str]:
        env = {**os.environ}
        # wine rejects relative WINEPREFIX paths — always resolve to absolute.
        env["WINEPREFIX"] = str(self.prefix_dir.resolve())
        env["WINEDEBUG"] = env.get("WINEDEBUG", "-all")  # silence wine warnings
        env.pop("DISPLAY", None)                          # force headless
        return env

    def compress(
        self,
        variant: Variant,
        inputs: list[Path],
        out_archive: Path,
    ) -> EncodeResult:
        out_archive.parent.mkdir(parents=True, exist_ok=True)
        if out_archive.exists():
            out_archive.unlink()

        cmd = [
            "wine",
            str(self.exe_path),
            "a",
            "-y",
            "-ep1",                              # strip leading paths from stored names
        ]
        method = _method_flag(variant)
        if method:
            cmd.append(method)
        if variant.solid:
            cmd.append("-s")
        if variant.password:
            cmd.append(f"-p{variant.password}")
        # rar treats `/path...` as a switch (DOS convention), so Linux paths
        # must be mapped to the wine drive letter. Wine auto-mounts `/` as
        # `Z:` so we just prefix with `Z:`.
        cmd.append(_to_wine_path(out_archive))
        cmd.extend(_to_wine_path(p) for p in inputs)

        proc, ms, rss = run_timed(cmd, env=self._env(), timeout=120.0)
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
        cmd = [
            "wine",
            str(self.exe_path),
            "e",
            "-y",
            "-o+",       # overwrite without prompt
        ]
        if password:
            cmd.append(f"-p{password}")
        cmd.append(_to_wine_path(archive))
        cmd.append(_to_wine_path(out_dir) + "/")

        proc, ms, rss = run_timed(cmd, env=self._env(), timeout=120.0)
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
        cmd = ["wine", str(self.exe_path), "t", "-y"]
        if password:
            cmd.append(f"-p{password}")
        cmd.append(_to_wine_path(archive))
        proc, _, _ = run_timed(cmd, env=self._env(), timeout=120.0)
        return proc.returncode


def _to_wine_path(p: Path | str) -> str:
    """Convert a host path to the Z: drive form wine RAR expects.

    Always resolves to absolute first (relative paths starting with `r` etc.
    would be misinterpreted by `rar` as switches without the `Z:` prefix).
    `/foo/bar` -> `Z:/foo/bar`. `Z:` is wine's auto-mount of host root.
    """
    abs_p = Path(p).resolve()
    return "Z:" + str(abs_p)


def _method_flag(variant: Variant) -> str | None:
    """Map our variant.method to a `rar`-compatible -m switch.

    'stored'        -> -m0
    'lz-N' (N=1..5) -> -mN
    'compressed'    -> default (no flag, RAR picks -m3)
    'ppmd'          -> caller responsibility (RAR 2.x doesn't expose it via
                       a CLI flag; rars handles it via --ppmd; for native
                       rar 3.x+ use `-mctext+` on top of the level switch).
    """
    return f"-m{variant.method_int}" if 0 <= variant.method_int <= 5 else None
