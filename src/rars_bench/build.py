"""`--rars-ref`: build any rars tag, branch or commit into a cached binary.

The cache directory holds one clone of rars (`src/`), one cargo target
directory (`target/`), and the binaries, one per commit (`bin/rars-<commit>`
with a `.json` beside it saying what was built and how). A commit already
built is never built again. The build is the one the release baseline used:
`cargo build --release --locked -p rars-cli --bin rars`, with the checkout's
own toolchain and release profile.

A clone that is not a working git checkout (a dangling worktree whose `.git`
names a directory that has gone, a half-finished clone, a repository git
cannot read) is deleted and cloned again instead of failing halfway through
a fetch.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
from pathlib import Path

DEFAULT_REPO = "https://github.com/bitplane/rars.git"
BUILD_ARGS = ["build", "--release", "--locked", "-p", "rars-cli", "--bin", "rars"]


def _git(cwd: Path | None, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *(["-C", str(cwd)] if cwd else []), *args], capture_output=True, text=True,
                          check=check, stdin=subprocess.DEVNULL)


def healthy(src: Path) -> bool:
    """Whether src is a git checkout git can read, rooted at src itself."""
    if not (src / ".git").exists():
        return False
    top = _git(src, "rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != src.resolve():
        return False
    return _git(src, "rev-parse", "--verify", "--quiet", "HEAD^{commit}").returncode == 0


def ensure_clone(src: Path, repo: str, log=print) -> None:
    if src.exists() and not healthy(src):
        log(f"  rars checkout at {src} is broken; cloning it again")
        shutil.rmtree(src)
    if not src.exists():
        src.parent.mkdir(parents=True, exist_ok=True)
        _git(None, "clone", "--quiet", repo, str(src), check=True)
    fetch = _git(src, "fetch", "--quiet", "--force", "--tags", "origin", "+refs/heads/*:refs/remotes/origin/*")
    if fetch.returncode != 0:
        log(f"  fetch failed ({fetch.stderr.strip()[:200]}); cloning again")
        shutil.rmtree(src)
        _git(None, "clone", "--quiet", repo, str(src), check=True)


def resolve(src: Path, ref: str) -> str:
    for candidate in (f"refs/tags/{ref}", f"origin/{ref}", ref):
        done = _git(src, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        if done.returncode == 0:
            return done.stdout.strip()
    raise SystemExit(f"error: rars ref {ref!r} is not a tag, branch or commit of the clone at {src}")


def find_cargo() -> str:
    found = shutil.which("cargo") or str(Path.home() / ".cargo/bin/cargo")
    if not Path(found).is_file():
        raise SystemExit("error: cargo not found on PATH or in ~/.cargo/bin")
    return found


def build(ref: str, cache: Path, repo: str = DEFAULT_REPO, cargo: str | None = None, log=print) -> tuple[Path, dict]:
    """The rars binary for `ref`, built once per commit. Returns its path and what was built."""
    cache = cache.resolve()
    src = cache / "src"
    ensure_clone(src, repo, log)
    commit = resolve(src, ref)
    binary = cache / "bin" / f"rars-{commit[:12]}"
    meta_path = binary.with_suffix(".json")
    if binary.is_file() and meta_path.is_file():
        info = json.loads(meta_path.read_text())
        log(f"  rars {ref} = {commit[:12]}, cached at {binary}")
        return binary, {**info, "ref": ref}
    _git(src, "checkout", "--quiet", "--force", "--detach", commit, check=True)
    _git(src, "clean", "-fdq", check=True)
    cargo = cargo or find_cargo()
    env = {**os.environ, "CARGO_TARGET_DIR": str(cache / "target")}
    cargo_dir = str(Path(cargo).parent)
    env["PATH"] = cargo_dir + os.pathsep + env.get("PATH", "")
    log(f"  building rars {ref} = {commit[:12]}: cargo {' '.join(BUILD_ARGS)}")
    done = subprocess.run([cargo, *BUILD_ARGS], cwd=src, env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    built = cache / "target" / "release" / "rars"
    if done.returncode != 0 or not built.is_file():
        raise SystemExit(f"error: building rars {ref} failed:\n{done.stderr[-2000:]}")
    binary.parent.mkdir(parents=True, exist_ok=True)
    tmp = binary.with_suffix(".part")
    shutil.copy2(built, tmp)
    tmp.rename(binary)
    tag = _git(src, "describe", "--tags", "--exact-match", commit)
    rustc = subprocess.run([str(Path(cargo).with_name("rustc")) if Path(cargo).with_name("rustc").is_file()
                            else "rustc", "-V"], capture_output=True, text=True, cwd=src, env=env)
    info = {
        "commit": commit,
        "commit_date": _git(src, "log", "-1", "--format=%cI", commit).stdout.strip(),
        "tag": tag.stdout.strip() if tag.returncode == 0 else None,
        "describe": _git(src, "describe", "--tags", "--always", commit).stdout.strip(),
        "build": f"cargo {' '.join(BUILD_ARGS)}",
        "rustc": rustc.stdout.strip() if rustc.returncode == 0 else "unknown",
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    meta_path.write_text(json.dumps(info, indent=1))
    return binary, {**info, "ref": ref}


def add_args(p) -> None:
    p.add_argument("--rars-ref", default=None,
                   help="build this rars tag, branch or commit (cached per commit) instead of --rars-bin")
    p.add_argument("--rars-repo", default=DEFAULT_REPO, help="where --rars-ref clones rars from")
    p.add_argument("--rars-builds", type=Path, default=Path(os.environ.get("RARS_BENCH_BUILDS", ".rars-builds")),
                   help="clone, cargo target and built binaries for --rars-ref")


def resolve_rars(args) -> Path | None:
    """The binary under test: built from --rars-ref, else --rars-bin. Sets args.rars_build for a built one."""
    if getattr(args, "rars_ref", None):
        binary, info = build(args.rars_ref, args.rars_builds, args.rars_repo)
        args.rars_build = info
        return binary
    args.rars_build = {}
    from .harness import _resolve_rars_bin
    return _resolve_rars_bin(args.rars_bin)
