"""Deterministic release-bench inputs, built once into a cache directory.

Everything comes from the machine's own `/usr` or from a seeded generator, so
nothing here is ever committed. File selection is a seeded shuffle of a sorted
walk, re-sorted by path once chosen: the same `/usr` gives the same bytes, and
a chosen set keeps a source tree's locality rather than a random interleave.
Each input records a digest of its manifest, so two runs can tell whether they
read the same inputs; a package upgrade on the machine changes `/usr` and with
it the digest. Inputs are reused while their `.done` marker exists, so a rars
or reference change never rebuilds them.

The reference archives the read scenarios use (`rar 7.12` output) are cached
here too, keyed on the rar binary's hash.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import stat
import subprocess
import tarfile
import zlib
from pathlib import Path

TEXT_EXT = {".py", ".pl", ".pm", ".h", ".hpp", ".hh", ".c", ".cc", ".txt", ".html", ".htm", ".xml",
            ".json", ".js", ".css", ".md", ".rst", ".sh", ".tcl", ".rb", ".lua", ".el", ".tex",
            ".svg", ".po", ".desktop", ".conf", ".ini", ".yaml", ".yml", ".csv", ".mo"}
PACKED_EXT = {".gz", ".xz", ".zst", ".bz2", ".png", ".jpg", ".jpeg", ".zip", ".jar", ".woff",
              ".woff2", ".deb", ".whl", ".lz4", ".7z", ".ogg", ".mp3", ".webp"}
TEXT_ROOTS = ["/usr/include", "/usr/share", "/usr/lib/python3"]
BIN_ROOTS = ["/usr/bin", "/usr/sbin", "/usr/libexec", "/usr/lib/x86_64-linux-gnu"]
PACKED_ROOTS = ["/usr/share", "/usr/lib"]
BIG_ROOTS = ["/usr/lib", "/usr/share"]
SMALL_ROOTS = ["/usr/share", "/usr/include", "/usr/lib/python3"]


def walk(roots: list[str], max_size: int, keep=lambda p, size: True) -> list[tuple[str, int]]:
    """Regular, readable files under the roots, sorted, no symlinks followed."""
    found: dict[str, int] = {}
    for root in roots:
        for base in sorted(Path("/").glob(root.lstrip("/") + "*")) if root.endswith("python3") else [Path(root)]:
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames.sort()
                for name in sorted(filenames):
                    path = os.path.join(dirpath, name)
                    try:
                        st = os.lstat(path)
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode) or st.st_size == 0 or st.st_size > max_size:
                        continue
                    if not os.access(path, os.R_OK) or not keep(path, st.st_size):
                        continue
                    found[path] = st.st_size
    return sorted(found.items())


def choose(files: list[tuple[str, int]], seed: int, target_bytes: int | None = None,
           count: int | None = None) -> list[tuple[str, int]]:
    order = list(files)
    random.Random(seed).shuffle(order)
    chosen, total = [], 0
    for path, size in order:
        if count is not None and len(chosen) >= count:
            break
        if target_bytes is not None and total + size > target_bytes:
            continue
        chosen.append((path, size))
        total += size
        if target_bytes is not None and total >= target_bytes * 0.999:
            break
    return sorted(chosen)


def is_elf(path: str, _size: int) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def ext_in(exts: set[str]):
    return lambda path, _size: os.path.splitext(path)[1].lower() in exts


def _tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.mtime, info.uid, info.gid, info.uname, info.gname = 0, 0, 0, "", ""
    info.mode = 0o755 if info.mode & 0o111 else 0o644
    return info


def write_tar(dest: Path, files: list[tuple[str, int]]) -> None:
    tmp = dest.with_suffix(".part")
    with tarfile.open(tmp, "w", format=tarfile.GNU_FORMAT) as tar:
        for path, _ in files:
            try:
                tar.add(path, arcname=path.lstrip("/"), recursive=False, filter=_tarinfo)
            except OSError:
                continue
    tmp.rename(dest)


def write_random(dest: Path, size: int, seed: int) -> None:
    rng = random.Random(seed)
    tmp = dest.with_suffix(".part")
    with tmp.open("wb") as f:
        left = size
        while left:
            n = min(left, 16 << 20)
            f.write(rng.randbytes(n))
            left -= n
    tmp.rename(dest)


def crc_file(path: Path) -> int:
    crc = 0
    with path.open("rb") as f:
        while chunk := f.read(4 << 20):
            crc = zlib.crc32(chunk, crc)
    return crc


def manifest(root: Path) -> dict[str, list[int]]:
    """relative path -> [size, crc32] for every regular file under root."""
    out = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        out[path.relative_to(root).as_posix()] = [path.stat().st_size, crc_file(path)]
    return out


def digest(entries: dict[str, list[int]]) -> str:
    blob = json.dumps(entries, sort_keys=True).encode()
    return f"{zlib.crc32(blob):08x}-{len(entries)}-{sum(v[0] for v in entries.values())}"


class Inputs:
    """Lazily built inputs under `root`; each `get_*` builds once, then reuses."""

    def __init__(self, root: Path, scale: dict, log=print):
        self.root = root
        self.scale = scale
        self.log = log
        root.mkdir(parents=True, exist_ok=True)

    def _cached(self, name: str, build) -> dict:
        """Build `name` (a dir under root) with build(dir) unless already done."""
        target = self.root / name
        done = self.root / f"{name}.done"
        if done.is_file():
            return json.loads(done.read_text())
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        self.log(f"  building input {name} ...")
        build(target)
        entries = manifest(target)
        info = {"name": name, "path": str(target), "files": len(entries),
                "bytes": sum(v[0] for v in entries.values()), "digest": digest(entries)}
        (self.root / f"{name}.manifest.json").write_text(json.dumps(entries))
        done.write_text(json.dumps(info))
        return info

    def manifest_of(self, name: str) -> dict[str, list[int]]:
        return json.loads((self.root / f"{name}.manifest.json").read_text())

    def payload(self) -> dict:
        """One seeded-random member: a stored Usenet-style set carries already-compressed media."""
        size = self.scale["volume_bytes"]
        return self._cached(f"payload-{size}", lambda d: write_random(d / "payload.bin", size, 505))

    def random(self) -> dict:
        size = self.scale["random_bytes"]
        return self._cached(f"random-{size}", lambda d: write_random(d / "random.bin", size, 513))

    def big(self) -> dict:
        """One tar of /usr/lib and /usr/share: code, text and some compressed data, as backups are."""
        size = self.scale["big_bytes"]

        def build(d: Path) -> None:
            files = choose(walk(BIG_ROOTS, 64 << 20), seed=498, target_bytes=size)
            write_tar(d / "usr.tar", files)
        return self._cached(f"big-{size}", build)

    def classes(self) -> dict[str, dict]:
        """The mixed corpus as three classes, one tar each, so each can be scored on its own."""
        size = self.scale["class_bytes"]
        specs = {
            "text": (TEXT_ROOTS, ext_in(TEXT_EXT), 4 << 20),
            "binary": (BIN_ROOTS, is_elf, 32 << 20),
            "compressed": (PACKED_ROOTS, ext_in(PACKED_EXT), 32 << 20),
        }
        out = {}
        for seed, (cls, (roots, keep, cap)) in enumerate(sorted(specs.items()), start=1):
            def build(d: Path, roots=roots, keep=keep, cap=cap, seed=seed, cls=cls) -> None:
                write_tar(d / f"{cls}.tar", choose(walk(roots, cap, keep), seed=seed, target_bytes=size))
            out[cls] = self._cached(f"class-{cls}-{size}", build)
        return out

    def small_files(self) -> dict:
        """N files of at most 64 KiB, copied with their /usr paths, as a legacy archive of a tree holds."""
        count = self.scale["small_files"]

        def build(d: Path) -> None:
            files = choose(walk(SMALL_ROOTS, 64 << 10), seed=494, count=count)
            tree = d / "tree"
            for path, _ in files:
                dest = tree / path.lstrip("/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
        return self._cached(f"small-{count}", build)

    def reference_archive(self, name: str, rar: Path, rar_sha: str, args: list[str],
                          cwd: Path, members: list[str]) -> Path:
        """An archive written by the reference `rar`, cached per rar binary.

        Returns the archive's directory; volumes land beside each other in it.
        """
        out = self.root / "ref-archives" / f"{name}-{rar_sha[:12]}"
        done = out / ".done"
        if done.is_file():
            return out
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        self.log(f"  writing reference archive {name} with {rar.name} ...")
        subprocess.run([str(rar), "a", "-idq", *args, str(out / f"{name}.rar"), *members],
                       cwd=cwd, check=True, stdin=subprocess.DEVNULL)
        done.write_text("ok")
        return out


def first_volume(directory: Path) -> Path:
    """The archive to open: the only .rar, or the first part of a set."""
    archives = sorted(directory.glob("*.rar"))
    if not archives:
        raise FileNotFoundError(f"no archive in {directory}")
    return archives[0]


def archive_bytes(directory: Path) -> int:
    return sum(p.stat().st_size for p in directory.glob("*.rar"))


def compare_tree(out: Path, expected: dict[str, list[int]], strip: str = "") -> tuple[bool, str]:
    """Whether `out` holds exactly the expected files, by size and CRC32."""
    got = manifest(out)
    if strip:
        got = {k[len(strip):]: v for k, v in got.items() if k.startswith(strip)}
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    wrong = sorted(k for k in set(expected) & set(got) if expected[k] != got[k])
    if not (missing or extra or wrong):
        return True, f"{len(got)} files match"
    parts = []
    for label, items in (("missing", missing), ("extra", extra), ("differ", wrong)):
        if items:
            parts.append(f"{len(items)} {label} (e.g. {items[0]})")
    return False, "; ".join(parts)
