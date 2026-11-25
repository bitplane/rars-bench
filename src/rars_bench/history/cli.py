"""`rars-bench history-import`: bring a release-bench result from before the history store into it."""
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


def run_import(args: argparse.Namespace) -> int:
    result = json.loads(args.result.read_text())
    prof = _machine.profile(args.workdir)
    # The run predates pinning: record what it could use, not a set it never ran on.
    prof["cpu_sets"] = None
    prof["pinned"] = False
    extra = {"imported_from": args.result.name}
    if args.commit_date:
        extra["commit_date"] = args.commit_date
    record = store.from_release(result, label=args.label, machine=prof, load=None, note=args.note,
                                rars_extra=extra)
    print(f"wrote {store.write(args.history_root, record)} ({len(record['cells'])} cells)")
    return 0
