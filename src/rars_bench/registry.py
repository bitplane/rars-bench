"""Every tool rars is measured against: how to find it, how to read its version, and which cells it is the reference for.

A ratio in the history is rars over one named tool. Two results line up a
cell's ratios only when the cell names the same tool in both, and the history
report flags a tool whose version or hash changed between them, since the
same name can then mean a different binary.

Adding a reference (7-Zip, bsdtar, rarfast, ...) is one `Tool` entry here
plus a driver or scenario that runs it; nothing else keys on the list.
Paths come from the command line (`args`), so each entry's `find` reads the
switch that names its binary.
"""
from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .release.measure import banner, sha256_file


@dataclass(frozen=True)
class Tool:
    name: str                                     # stable; never carries a version
    runs: str                                     # native | wine | dosbox
    find: Callable[[argparse.Namespace], Path | None]
    version: Callable[[Path], str]                # read from the binary, or fixed for an emulated one
    cells: tuple[str, ...] = field(default=())    # cell-id prefixes this tool is the reference for
    role: str = "reference"

    def is_reference_for(self, cell_id: str) -> bool:
        return any(cell_id.startswith(prefix) for prefix in self.cells)


def _arg(name: str) -> Callable[[argparse.Namespace], Path | None]:
    def find(args: argparse.Namespace) -> Path | None:
        path = getattr(args, name, None)
        return Path(path) if path is not None and Path(path).is_file() else None
    return find


def _native(path: Path) -> str:
    return banner([str(path)])


def _wine(fmt: str) -> Callable[[argparse.Namespace], Path | None]:
    def find(args: argparse.Namespace) -> Path | None:
        from .harness import _WINE_ORACLES
        cfg = _WINE_ORACLES[fmt]
        root = getattr(args, "wineprefix_root", None)
        exe = Path(root) / cfg.prefix_subdir / cfg.rar_relpath if root else None
        return exe if exe is not None and exe.is_file() else None
    return find


def _fixed(text: str) -> Callable[[Path], str]:
    return lambda _path: text


READS = ("volumes-rar/", "volumes-rars/test", "volumes-rars/extract", "rar5-big/", "small-files/", "discmaster/")

REGISTRY: list[Tool] = [
    Tool("unrar", "native", _arg("unrar_bin"), _native, READS + ("check/unrar",)),
    Tool("rar", "native", _arg("rar_bin"), _native, ("create-rar5/", "create-random/", "volumes-rars/create")),
    Tool("rar393", "native", _arg("rar393_bin"), _native, ("create-rar4/",)),
    Tool("ref-rar14", "dosbox", _arg("rar1402_bin"), _fixed("RAR 1.402 (DOSBox-X; sizes only)"), ("size/rar14/",)),
    Tool("ref-rar20", "dosbox", _arg("rar250_bin"), _fixed("RAR 2.50 (DOSBox-X; sizes only)"), ("size/rar20/",)),
    Tool("ref-rar29", "wine", _wine("rar29"), _fixed("WinRAR 2.90 (wine; sizes only)"), ("size/rar29/",)),
    Tool("ref-rar30", "wine", _wine("rar30"), _fixed("WinRAR 3.00 (wine; sizes only)"), ("size/rar30/",)),
    Tool("ref-rar40", "wine", _wine("rar40"), _fixed("WinRAR 4.20 (wine; sizes only)"), ("size/rar40/",)),
    Tool("ref-rar50", "wine", _wine("rar50"), _fixed("WinRAR 6.02 (wine; sizes only)"), ("size/rar50/",)),
    Tool("ref-rar70", "wine", _wine("rar70"), _fixed("WinRAR 7.21 (wine; sizes only)"), ("size/rar70/",)),
]

BY_NAME = {t.name: t for t in REGISTRY}


def reference_for(cell_id: str, registry: list[Tool] = REGISTRY) -> str | None:
    """The tool a cell's ratios are taken against: the longest matching prefix wins."""
    best, length = None, -1
    for tool in registry:
        for prefix in tool.cells:
            if cell_id.startswith(prefix) and len(prefix) > length:
                best, length = tool.name, len(prefix)
    return best


def describe(args: argparse.Namespace, registry: list[Tool] = REGISTRY, names: list[str] | None = None) -> dict:
    """name -> {version, sha256, runs} for every registered tool, `not found` for missing ones."""
    out = {}
    for tool in registry:
        if names is not None and tool.name not in names:
            continue
        path = tool.find(args)
        if path is None:
            out[tool.name] = {"version": "not found", "runs": tool.runs}
            continue
        out[tool.name] = {"version": tool.version(path), "sha256": sha256_file(path), "runs": tool.runs,
                          "path": str(path)}
    for name, cmd in (("wine", ["wine", "--version"]), ("dosbox-x", ["dosbox-x", "-version"])):
        if shutil.which(cmd[0]) and (names is None or name in names):
            out[name] = {"version": banner(cmd, r"."), "runs": "emulator"}
    return out


def tool_changes(old: dict, new: dict) -> list[str]:
    """One line per tool in both results whose version or hash differs."""
    lines = []
    for name in sorted(set(old) & set(new)):
        a, b = old[name], new[name]
        if a.get("version") != b.get("version"):
            lines.append(f"{name}: version {a.get('version')} -> {b.get('version')}")
        elif a.get("sha256") and b.get("sha256") and a["sha256"] != b["sha256"]:
            lines.append(f"{name}: same version, sha256 {a['sha256'][:12]} -> {b['sha256'][:12]}")
    return lines
