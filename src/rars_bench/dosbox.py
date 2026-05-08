"""DosboxSession — wraps dosbox-x for running DOS-era RAR binaries.

Caller workflow:
    s = DosboxSession(work_dir)
    s.add_binary(Path("RAR1_402.EXE"))                 # copies as RAR.EXE by default
    rar_in = s.add_input(Path("local/foo.txt"))        # returns 8.3 DOS name
    s.add_command(f"rar a OUT.RAR {rar_in}")
    result = s.run()                                   # subprocess + parse RESULTS.TXT
    archive_bytes = (s.dir / "OUT.RAR").read_bytes()

GO.BAT ends with `echo EXIT=%ERRORLEVEL% > RESULTS.TXT` so we get a parseable
status line. Caller is responsible for the working directory lifecycle —
we don't auto-clean.
"""
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

_DOS_BIN = "dosbox-x"


@dataclass(frozen=True)
class DosboxRunResult:
    elapsed_ms: float
    dosbox_returncode: int
    rar_exit: int                        # parsed from RESULTS.TXT; -1 on parse failure / timeout
    stdout: str
    stderr: str
    results_txt: str                     # raw RESULTS.TXT content, or '' if absent


@dataclass
class DosboxSession:
    dir: Path
    time_limit_s: int = 30
    run_timeout_s: int = 45
    _used_dos_names: set[str] = field(default_factory=set)
    _commands: list[str] = field(default_factory=list)
    _input_map: dict[Path, str] = field(default_factory=dict)
    _files_before_run: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def ensure_available() -> None:
        if shutil.which(_DOS_BIN) is None:
            raise RuntimeError(
                "dosbox-x not found on PATH. Install with: sudo apt install dosbox-x"
            )

    def add_binary(self, src: Path, dos_name: str = "RAR.EXE") -> str:
        if not src.is_file():
            raise FileNotFoundError(f"binary not found: {src}")
        self._reserve_name(dos_name)
        shutil.copyfile(src, self.dir / dos_name)
        return dos_name

    def add_input(self, src: Path, dos_name: str | None = None) -> str:
        if not src.is_file():
            raise FileNotFoundError(f"input not found: {src}")
        if dos_name is None:
            dos_name = self._derive_dos_name(src.name)
        else:
            self._reserve_name(dos_name)
        shutil.copyfile(src, self.dir / dos_name)
        self._input_map[src] = dos_name
        return dos_name

    def add_command(self, line: str) -> None:
        self._commands.append(line)

    def file(self, dos_name: str) -> Path:
        return self.dir / dos_name

    def list_new_files(self) -> list[Path]:
        """Files that appeared in the session dir during the most recent run.

        Returns the same set the runner wants to scan as 'extracted output'.
        Excludes RAR.EXE, GO.BAT, RESULTS.TXT, and inputs registered via add_input.
        """
        excluded = {"RAR.EXE", "GO.BAT", "RESULTS.TXT", *self._used_dos_names}
        out: list[Path] = []
        for p in sorted(self.dir.iterdir()):
            if not p.is_file():
                continue
            if p.name in excluded:
                continue
            if p.name in self._files_before_run:
                continue
            out.append(p)
        return out

    def run(self) -> DosboxRunResult:
        DosboxSession.ensure_available()
        self._write_go_bat()
        # Snapshot files just before run so list_new_files() can subtract.
        self._files_before_run = {p.name for p in self.dir.iterdir() if p.is_file()}
        # GO.BAT itself was just written — make sure it's in the snapshot.
        self._files_before_run.add("GO.BAT")
        cmd = [
            _DOS_BIN,
            "-silent",
            "-exit",
            "-nogui",
            "-nomenu",
            "-time-limit",
            str(self.time_limit_s),
            "-c",
            f"mount c {self.dir}",
            "-c",
            "c:",
            "-c",
            "go.bat",
            "-c",
            "exit",
        ]
        env = {
            **os.environ,
            "SDL_VIDEODRIVER": "dummy",
            "SDL_AUDIODRIVER": "dummy",
        }
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                env=env,
                timeout=self.run_timeout_s,
            )
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            returncode = proc.returncode
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
            returncode = -1
        elapsed = (time.monotonic() - t0) * 1000.0

        results_txt = ""
        rar_exit = -1
        rt = self.dir / "RESULTS.TXT"
        if rt.is_file():
            results_txt = rt.read_text(errors="replace")
            rar_exit = _parse_exit(results_txt)
        return DosboxRunResult(
            elapsed_ms=elapsed,
            dosbox_returncode=returncode,
            rar_exit=rar_exit,
            stdout=stdout,
            stderr=stderr,
            results_txt=results_txt,
        )

    def reset_commands(self) -> None:
        """Allow the caller to reuse a session for a second run (e.g. extract after compress)."""
        self._commands.clear()

    # internals -----------------------------------------------------------------
    def _write_go_bat(self) -> None:
        # DOSBox-X's COMMAND.COM gotchas: %ERRORLEVEL% is NOT expanded inside
        # echo, and `if errorlevel N echo X > FILE` opens FILE for write
        # before evaluating the conditional (truncating on the false branch).
        # Workaround: goto-based fail-fast — STATUS=OK only if every command
        # returned errorlevel 0; otherwise STATUS=FAIL. The whole-batch
        # semantics matter when callers stage multiple `rar a OUT.RAR FILEn`
        # commands to bypass the DOS ~127-byte command-line limit.
        lines = ["@echo off"]
        for cmd in self._commands:
            lines.append(cmd)
            lines.append("if errorlevel 1 goto failed")
        lines.append("echo STATUS=OK > RESULTS.TXT")
        lines.append("goto end")
        lines.append(":failed")
        lines.append("echo STATUS=FAIL > RESULTS.TXT")
        lines.append(":end")
        lines.append("exit")
        text = "\r\n".join(lines) + "\r\n"
        (self.dir / "GO.BAT").write_bytes(text.encode("ascii"))

    def _derive_dos_name(self, src_name: str) -> str:
        stem, _, ext = src_name.rpartition(".")
        if not stem and ext:    # name had no dot — treat whole thing as stem
            stem, ext = ext, ""
        stem = _sanitize(stem)[:8] or "FILE"
        ext = _sanitize(ext)[:3]
        base = f"{stem}.{ext}" if ext else stem
        if base not in self._used_dos_names:
            self._used_dos_names.add(base)
            return base
        # Collision: append ~N before the dot, shrinking stem as needed.
        for n in range(1, 100):
            suffix = f"~{n}"
            keep = max(1, 8 - len(suffix))
            candidate = f"{stem[:keep]}{suffix}.{ext}" if ext else f"{stem[:keep]}{suffix}"
            if candidate not in self._used_dos_names:
                self._used_dos_names.add(candidate)
                return candidate
        raise RuntimeError(f"could not allocate 8.3 DOS name for {src_name}")

    def _reserve_name(self, dos_name: str) -> None:
        if dos_name in self._used_dos_names:
            raise ValueError(f"DOS name already used: {dos_name}")
        self._used_dos_names.add(dos_name)


_SANITIZE_RE = re.compile(r"[^A-Z0-9_]")


def _sanitize(s: str) -> str:
    return _SANITIZE_RE.sub("_", s.upper())


_STATUS_RE = re.compile(r"^STATUS\s*=\s*(OK|FAIL)", re.MULTILINE)


def _parse_exit(text: str) -> int:
    """Map the LAST STATUS line: OK -> 0, FAIL -> 1, missing -> -1.

    The "last" semantics matter when a session runs multiple commands —
    callers using a single command get exactly one STATUS line and the
    function still does the right thing.
    """
    matches = _STATUS_RE.findall(text)
    if not matches:
        return -1
    last = matches[-1]
    return 0 if last == "OK" else 1
