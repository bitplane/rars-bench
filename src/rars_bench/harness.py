"""rars-bench CLI entry point: `sweep`, `triage`, `report`, `backfill-packed`, `release` and `release-compare`."""
import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from . import triage as _triage
from .drivers import DosRarDriver, RarsDriver, WineRarDriver
from .formats.rar20 import RAR20_DEFAULT
from .formats.rar30 import RAR30_DEFAULT, RAR30_SOLID
from .formats.rar40 import RAR40_DEFAULT, RAR40_SOLID
from .formats.rar50 import RAR50_DEFAULT
from .formats.rar70 import RAR70_DEFAULT
from .formats.rar29 import RAR29_DEFAULT, RAR29_SOLID
from .runner import sweep
from .variants import RAR14_SLICE


_VARIANT_SETS: dict[str, dict[str, list]] = {
    "rar14": {"default": RAR14_SLICE},
    "rar20": {"default": RAR20_DEFAULT},
    "rar29": {"default": RAR29_DEFAULT, "solid": RAR29_SOLID},
    "rar30": {"default": RAR30_DEFAULT, "solid": RAR30_SOLID},
    "rar40": {"default": RAR40_DEFAULT, "solid": RAR40_SOLID},
    "rar50": {"default": RAR50_DEFAULT},
    "rar70": {"default": RAR70_DEFAULT},
}


@dataclass(frozen=True)
class _WineOracle:
    prefix_subdir: str                              # under _refs/wineprefixes/
    rar_relpath: str                                # under prefix
    version: str

    def relative_exe(self) -> Path:
        return Path(self.rar_relpath)


@dataclass(frozen=True)
class _DosOracle:
    version: str            # rendered into the schema's encoder_version
    arg_attr: str           # argparse Namespace attribute holding the binary path


_DOS_ORACLES: dict[str, _DosOracle] = {
    "rar14": _DosOracle(version="1.402", arg_attr="rar1402_bin"),
    "rar20": _DosOracle(version="2.50",  arg_attr="rar250_bin"),
}


# Map format -> wine oracle config. Each entry resolves to a WineRarDriver
# in _build_oracle.
_WINE_ORACLES: dict[str, _WineOracle] = {
    "rar29": _WineOracle("winrar290", "drive_c/Program Files (x86)/WinRAR/Rar.exe", "2.90"),
    "rar30": _WineOracle("winrar300", "drive_c/Program Files (x86)/WinRAR/Rar.exe", "3.00"),
    "rar40": _WineOracle("winrar420", "drive_c/Program Files (x86)/WinRAR/Rar.exe", "4.20"),
    "rar50": _WineOracle("winrar602", "drive_c/Program Files (x86)/WinRAR/Rar.exe", "6.02"),
    # winrar721 is 64-bit, so no `(x86)`.
    "rar70": _WineOracle("winrar721", "drive_c/Program Files/WinRAR/Rar.exe", "7.21"),
}


def _build_oracle(fmt: str, args: argparse.Namespace):
    dos_cfg = _DOS_ORACLES.get(fmt)
    if dos_cfg is not None:
        bin_path = getattr(args, dos_cfg.arg_attr)
        if not bin_path.is_file():
            return None, (
                f"{dos_cfg.version} binary not found at {bin_path}. "
                f"Run scripts/copy-refbins.sh or pass --{dos_cfg.arg_attr.replace('_', '-')}."
            )
        if shutil.which("dosbox-x") is None:
            return None, (
                "dosbox-x not found on PATH. Install with: sudo apt install dosbox-x"
            )
        return DosRarDriver(bin_path, version=dos_cfg.version), None

    cfg = _WINE_ORACLES.get(fmt)
    if cfg is None:
        return None, f"no oracle configured for format {fmt}"
    prefix = args.wineprefix_root / cfg.prefix_subdir
    rar_exe = prefix / cfg.relative_exe()
    if not rar_exe.is_file():
        return None, (
            f"{cfg.prefix_subdir} prefix not populated at {prefix}. "
            "Run scripts/copy-refbins.sh."
        )
    if shutil.which("wine") is None:
        return None, "wine not found on PATH. Install with: sudo apt install wine"
    return (
        WineRarDriver(
            prefix_dir=prefix,
            relative_exe=cfg.relative_exe(),
            version=cfg.version,
        ),
        None,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="rars-bench")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sweep", help="run a sweep over a corpus")
    s.add_argument("--format", required=True, choices=sorted(_VARIANT_SETS))
    s.add_argument("--variants", default="default")
    s.add_argument(
        "--corpus",
        action="append",
        default=None,
        help="corpus class to sweep; repeat for multiple, or use 'all'",
    )
    s.add_argument("--corpora-root", type=Path, default=Path("corpora"))
    s.add_argument("--out", required=True, type=Path)
    s.add_argument("--rars-bin", type=Path, default=None)
    s.add_argument(
        "--rar1402-bin",
        type=Path,
        default=Path("_refs/rarbins/RAR1_402.EXE"),
    )
    s.add_argument(
        "--rar250-bin",
        type=Path,
        default=Path("_refs/rarbins/RAR250.EXE"),
    )
    s.add_argument(
        "--wineprefix-root",
        type=Path,
        default=Path("_refs/wineprefixes"),
        help="dir containing winrar* prefixes (one per oracle)",
    )
    s.add_argument("--work-root", type=Path, default=None)
    s.add_argument(
        "--cache-root",
        type=Path,
        default=Path("_refs/cache"),
        help="dir for cached oracle (wine/dosbox) encode outputs",
    )
    s.add_argument(
        "--no-cache",
        action="store_true",
        help="disable oracle encode cache for this sweep",
    )
    s.add_argument("--skip-missing-drivers", action="store_true")

    t = sub.add_parser("triage", help="print rows from a results DB")
    t.add_argument("db", type=Path)
    t.add_argument(
        "--by",
        choices=["all", "ratio-gap", "failures"],
        default="all",
    )
    t.add_argument("--top", type=int, default=20)

    r = sub.add_parser("report", help="render results DBs as a standalone HTML report")
    r.add_argument("db", type=Path, nargs="+", help="one or more results DBs, rendered as stacked sections")
    r.add_argument(
        "--label",
        action="append",
        default=None,
        help="section heading for the matching DB; defaults to its filename",
    )
    r.add_argument("--out", type=Path, default=Path("results/report.html"))
    r.add_argument("--title", default=None)

    e = sub.add_parser("seed-edge", help="write the synthetic edge-case classes (corpora/edge-*)")
    e.add_argument("--corpora-root", type=Path, default=Path("corpora"))
    e.add_argument("--only", default=None, help="comma list of edge classes")

    b = sub.add_parser(
        "backfill-packed",
        help="fill packed_bytes on old rows from the archives still in the work tree",
    )
    b.add_argument("db", type=Path, nargs="+")
    b.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="the sweep's work tree; defaults to work/ beside each DB",
    )

    from .release import cli as _release

    _release.add_parsers(sub)
    from .history import cli as _history

    _history.add_parsers(sub)
    from . import quick as _quick

    _quick.add_parsers(sub)

    args = p.parse_args(argv)

    if args.cmd == "release":
        return _release.run(args)
    if args.cmd == "release-compare":
        return _release.run_compare(args)
    if args.cmd == "quick":
        return _quick.run(args)
    if args.cmd == "history-import":
        return _history.run_import(args)

    if args.cmd == "sweep":
        return _cmd_sweep(args)
    if args.cmd == "seed-edge":
        from .synth import seed_edge

        for cls, digest in seed_edge(args.corpora_root, args.only.split(",") if args.only else None).items():
            print(f"  {cls:18} {digest[:16]}")
        return 0
    if args.cmd == "triage":
        return _triage.run(args.db, args.by, args.top)
    if args.cmd == "backfill-packed":
        return _cmd_backfill(args)
    if args.cmd == "report":
        from . import report as _report

        labels = list(args.label or [])
        labels += [None] * (len(args.db) - len(labels))
        args.out.parent.mkdir(parents=True, exist_ok=True)
        written = _report.write_report(list(zip(args.db, labels)), args.out, args.title)
        print(f"wrote {written}")
        return 0
    return 2


def _cmd_sweep(args: argparse.Namespace) -> int:
    rars_bin = _resolve_rars_bin(args.rars_bin)
    if rars_bin is None and not args.skip_missing_drivers:
        print(
            "error: rars binary not found. Build with `cargo build --release` in ../rars/, "
            "set $RARS_BIN, or pass --rars-bin.",
            file=sys.stderr,
        )
        return 2

    drivers = []
    if rars_bin is not None:
        drivers.append(RarsDriver(rars_bin))

    oracle, oracle_err = _build_oracle(args.format, args)
    if oracle is not None:
        drivers.append(oracle)
    elif not args.skip_missing_drivers:
        print(f"error: {oracle_err}", file=sys.stderr)
        return 2

    if not drivers:
        print("error: no drivers to run", file=sys.stderr)
        return 2

    variants = _VARIANT_SETS[args.format].get(args.variants)
    if variants is None:
        print(
            f"error: unknown variant set '{args.variants}' for format {args.format}",
            file=sys.stderr,
        )
        return 2

    corpus_classes = _resolve_corpus_classes(args.corpus, args.corpora_root)
    if not corpus_classes:
        print("error: no corpus classes resolved", file=sys.stderr)
        return 2

    work_root = args.work_root or args.out.parent / "work"
    cache_root = None if args.no_cache else args.cache_root
    rows = sweep(
        db_path=args.out,
        corpus_root=args.corpora_root,
        corpus_classes=corpus_classes,
        variants=variants,
        drivers=drivers,
        work_root=work_root,
        cache_root=cache_root,
        skip_unavailable=args.skip_missing_drivers,
    )
    print(f"wrote {rows} rows to {args.out}")
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    from .packed import backfill

    keys = {
        (v.format, v.method_int, v.filter, int(v.solid), int(v.encrypted), v.dict_log): v.key
        for sets in _VARIANT_SETS.values()
        for variants in sets.values()
        for v in variants
    }
    for db in args.db:
        if not db.is_file():
            print(f"error: {db} not found", file=sys.stderr)
            return 2
        work_root = args.work_root or db.parent / "work"
        filled, missing = backfill(db, work_root, keys)
        print(f"{db}: filled {filled} rows, {missing} without a readable archive")
    return 0


#: Class-name prefix marking the tier whose members are big enough to reach
#: past a modern writer's dictionary and block sizes. The rest of the corpus
#: caps files at 64 KiB, which was sized for the RAR 1.4 sweep.
LARGE_TIER_PREFIX = "large-"
#: The synthetic edge-case classes (`synth.py`): up to 4 MiB, so not small-tier either.
EDGE_PREFIX = "edge-"


def _resolve_corpus_classes(arg: list[str] | None, corpora_root: Path) -> list[str]:
    """Resolve corpus selectors to class names.

    Besides `all` and explicit names, `small` and `large` select a size tier.
    The two answer different questions and cost very different amounts of time:
    nothing RAR 1.4 does is visible above a 64 KiB window, so `small` is where
    the older formats belong, and a RAR 5 dictionary, block size or filter
    region decision shows up nowhere except `large`.
    """
    if not arg:
        arg = ["all"]
    if not corpora_root.is_dir():
        return [] if {"all", "small", "large", "edge"} & set(arg) else list(arg)
    present = sorted(p.name for p in corpora_root.iterdir() if p.is_dir())

    resolved: list[str] = []
    for selector in arg:
        if selector == "all":
            resolved.extend(present)
        elif selector == "large":
            resolved.extend(c for c in present if c.startswith(LARGE_TIER_PREFIX))
        elif selector == "small":
            resolved.extend(c for c in present if not c.startswith((LARGE_TIER_PREFIX, EDGE_PREFIX)))
        elif selector == "edge":
            resolved.extend(c for c in present if c.startswith(EDGE_PREFIX))
        else:
            resolved.append(selector)
    return sorted(dict.fromkeys(resolved))


def _resolve_rars_bin(explicit: Path | None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    env = os.environ.get("RARS_BIN")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    # ../rars was true when this lived beside the checkout. It usually does
    # not any more, and the failure mode was a whole sweep skipped rather
    # than an error, so try the places a checkout actually turns up.
    roots = [Path("../rars"), Path.home() / "src/rars", Path(__file__).resolve().parents[3] / "rars"]
    for root in roots:
        for profile in ("release", "debug"):
            candidate = root / "target" / profile / "rars"
            if candidate.is_file():
                return candidate.resolve()
    which = shutil.which("rars")
    if which:
        return Path(which)
    return None
