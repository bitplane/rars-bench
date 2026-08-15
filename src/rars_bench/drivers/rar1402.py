"""DosRarDriver — drives a DOS RAR.EXE under DOSBox-X.

Used for the DOS-era oracles: RAR 1.402 (rar14 wire format) and RAR 2.50
(rar20 wire format, same as RAR4 family). The class is parameterised by
binary path + version label; the per-variant `-m{0..5}` flag covers level
selection (RAR 1.402 ignores -m1..-m5 — its writer has no level concept —
but accepts the flag without error).

Each compress/extract/test creates a fresh session directory under
out_archive.parent (or out_dir.parent), copies the binary + inputs in,
writes GO.BAT, runs DOSBox, and harvests results. The session dir is left
in place on success and on failure for triage.
"""
import secrets
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..dosbox import DosboxSession
from ..variants import Variant
from .base import DecodeResult, EncodeResult


@dataclass
class DosRarDriver:
    exe_path: Path
    name: str = "rar"
    version: str = "1.402"
    cacheable: bool = True
    _session_id: int = field(default=0, init=False)

    def available(self) -> bool:
        return self.exe_path.is_file() and shutil.which("dosbox-x") is not None

    def compress(
        self,
        variant: Variant,
        inputs: list[Path],
        out_archive: Path,
    ) -> EncodeResult:
        out_archive.parent.mkdir(parents=True, exist_ok=True)
        session_dir = self._session_dir(out_archive.parent, "encode")
        s = DosboxSession(session_dir)
        s.add_binary(self.exe_path, "RAR.EXE")
        dos_inputs = [s.add_input(p) for p in inputs]
        method_flag = _method_flag(variant)
        password_flag = f" -p{variant.password}" if variant.password else ""
        # DOS COMMAND.COM has a ~127-byte command line limit. With 8.3 names
        # averaging ~12 chars, ~10 inputs already exceed it and rar silently
        # archives only the first chunk that fits. Add files one at a time;
        # each `rar a` appends to OUT.RAR. Goto-based fail-fast in GO.BAT
        # surfaces the first error.
        for dos_input in dos_inputs:
            s.add_command(
                f"rar a{method_flag}{password_flag} OUT.RAR {dos_input}"
            )

        result = s.run()
        produced = session_dir / "OUT.RAR"
        if produced.is_file():
            shutil.copyfile(produced, out_archive)
            archive_bytes = out_archive.stat().st_size
        else:
            archive_bytes = 0

        return EncodeResult(
            archive_path=out_archive,
            archive_bytes=archive_bytes,
            exit_code=result.rar_exit,
            encode_ms=result.elapsed_ms,
            peak_rss_kib=None,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def extract(
        self,
        variant: Variant,
        archive: Path,
        out_dir: Path,
        password: str | None = None,
    ) -> DecodeResult:
        out_dir.mkdir(parents=True, exist_ok=True)
        # Session dir must live OUTSIDE out_dir so callers walking out_dir
        # recursively don't pick up RAR.EXE / GO.BAT / RESULTS.TXT / IN.RAR
        # as if they were extracted members.
        session_dir = self._session_dir(out_dir.parent, "extract")
        s = DosboxSession(session_dir)
        s.add_binary(self.exe_path, "RAR.EXE")
        archive_dos = s.add_input(archive, "IN.RAR")
        password_flag = f" -p{password}" if password else ""
        # `-y` answers Yes to all prompts; without it, RAR 1.402 hangs waiting
        # for keyboard input when the archive contains long names that collide
        # in 8.3 (e.g. `gpu-manager.log` + `gpu-manager-switch.log` both map
        # to `GPU-MANA.LOG`, triggering the overwrite prompt).
        s.add_command(f"rar e -y{password_flag} {archive_dos}")

        result = s.run()
        new_files = s.list_new_files()
        # Filter out IN.RAR itself (registered as input, but list_new excludes it
        # via _used_dos_names — keep this defence-in-depth comment).
        members: list[Path] = []
        for src in new_files:
            dest = out_dir / src.name
            shutil.copyfile(src, dest)
            members.append(dest)

        return DecodeResult(
            extracted_dir=out_dir,
            member_paths=sorted(members),
            exit_code=result.rar_exit,
            decode_ms=result.elapsed_ms,
            peak_rss_kib=None,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    def test(
        self,
        variant: Variant,
        archive: Path,
        password: str | None = None,
    ) -> int:
        session_dir = self._session_dir(archive.parent, "test")
        s = DosboxSession(session_dir)
        s.add_binary(self.exe_path, "RAR.EXE")
        archive_dos = s.add_input(archive, "IN.RAR")
        password_flag = f" -p{password}" if password else ""
        s.add_command(f"rar t -y{password_flag} {archive_dos}")
        result = s.run()
        return result.rar_exit

    def _session_dir(self, parent: Path, kind: str) -> Path:
        self._session_id += 1
        token = secrets.token_hex(3)
        d = parent / f".dos-{kind}-{self._session_id:03d}-{token}"
        d.mkdir(parents=True, exist_ok=False)
        return d


def _method_flag(variant: Variant) -> str:
    """Map our variant.method to a DOS RAR `-m` switch.

    'stored'                 -> ' -m0'
    'compressed' (rar14)     -> ''  (DOS RAR 1.402 default)
    'lz-N' for N in 1..5     -> ' -mN'
    anything else            -> ''
    """
    method = variant.method
    if method == "stored":
        return " -m0"
    if method.startswith("lz-"):
        suffix = method[3:]
        if suffix.isdigit() and 0 <= int(suffix) <= 5:
            return f" -m{suffix}"
    return ""


# Back-compat alias — original name was 1.402-specific.
Rar1402Driver = DosRarDriver
