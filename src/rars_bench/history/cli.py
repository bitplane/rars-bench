"""`rars-bench history` (the report) and `rars-bench history-import` (an older release result into the store)."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import machine as _machine
from . import store


def add_parsers(sub) -> None:
    i = sub.add_parser("history-import",
                       help="file an existing release-bench JSON into the history store; run it on the machine "
                            "that produced the result, whose profile it records")
    i.add_argument("result", type=Path)
    i.add_argument("--label", required=True, help="version directory, e.g. v0.13.1 or a commit's 8 hex digits")
    i.add_argument("--note", default=None, help="what the build was, kept in the record")
    i.add_argument("--commit-date", default=None, help="the rars commit's date, for ordering the history")
    i.add_argument("--history-root", type=Path, default=Path("results"))
    i.add_argument("--workdir", type=Path, default=Path("."), help="where the run wrote its scratch output")


def add_report_parser(sub) -> None:
    h = sub.add_parser("history", help="markdown table of cells against versions for one machine")
    h.add_argument("--history-root", type=Path, default=Path("results"))
    h.add_argument("--machine", default=None, help="machine id (default: this machine if it has records)")
    h.add_argument("--tier", default="all", help="quick, timed, or all (default)")
    h.add_argument("--other-machines", action="store_true",
                   help="fill versions this machine lacks from other machines, ratio-only")
    h.add_argument("--changed-only", action="store_true", help="only rows with something bold")
    h.add_argument("--threshold", type=float, default=0.05, help="ratio change that counts as moved")
    h.add_argument("--out", type=Path, default=None)


def run_report(args: argparse.Namespace) -> int:
    from . import report
    records = store.load_all(args.history_root)
    if not records:
        print(f"no history records under {args.history_root}")
        return 1
    here = _machine.machine_id(_machine.cpu_model())
    machine = args.machine or report.default_machine(records, here)
    tiers = sorted({r["tier"] for r in records}) if args.tier == "all" else [args.tier]
    text = "\n".join(report.render(records, machine=machine, tier=t, others=args.other_machines,
                                    threshold=args.threshold, changed_only=args.changed_only) for t in tiers)
    if args.out:
        args.out.write_text(text)
        print(f"wrote {args.out}")
    else:
        print(text, end="")
    return 0


def run_import(args: argparse.Namespace) -> int:
    result = json.loads(args.result.read_text())
    prof = _machine.profile(args.workdir)
    # The run predates pinning: record what it could use, not a set it never ran on.
    prof["cpu_sets"] = None
    prof["allowed_cpus"] = None
    prof["pinned"] = False
    extra = {"imported_from": args.result.name}
    if args.commit_date:
        extra["commit_date"] = args.commit_date
    record = store.from_release(result, label=args.label, machine=prof, load=None, note=args.note,
                                rars_extra=extra)
    print(f"wrote {store.write(args.history_root, record)} ({len(record['cells'])} cells)")
    return 0
