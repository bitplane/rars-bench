"""Reading and writing `results/<version>/<machine-id>-<tier>.json`, and turning a release-bench result into one.

A history record:

    {"schema": 1, "tier": "quick" | "timed" | "timed-tiny", "date": ..., "elapsed_s": ...,
     "rars": {"label", "version", "commit", "sha256", "note", ...},
     "machine": {profile, see machine.py}, "load": {"start": ..., "end": ...},
     "tools": {name: {"version", "sha256", ...}},
     "cells": {cell id: {"ref": tool name or None, "ok": bool, "metrics": {...},
                         "ref_metrics": {...}, "ratios": {...}}},
     "checks": {...}}

Cell ids carry the thread label (t1, t4, tmax), never a raw thread count, so
the same cell lines up on a 16-thread and a 32-thread machine. Ratios are
rars over the reference named in the cell; above 1 means rars is slower,
bigger or uses more memory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .machine import thread_label

SCHEMA = 1
TIERS = ("quick", "timed", "timed-tiny")


def record_path(root: Path, label: str, machine_id: str, tier: str) -> Path:
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; one of {TIERS}")
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", label):
        raise ValueError(f"version label {label!r} is not a plain directory name")
    return root / label / f"{machine_id}-{tier}.json"


def write(root: Path, record: dict) -> Path:
    path = record_path(root, record["rars"]["label"], record["machine"]["id"], record["tier"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
    return path


def load_all(root: Path) -> list[dict]:
    """Every history record under root, with `_path` set. Files from before the layout are skipped."""
    out = []
    for path in sorted(root.glob("*/*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("schema") != SCHEMA or "cells" not in record:
            continue
        record["_path"] = str(path)
        out.append(record)
    return out


def ratio(num: float | None, den: float | None) -> float | None:
    if num is None or not den:
        return None
    return round(num / den, 5)


# --- from a release-bench result -----------------------------------------------------------------

#: The release bench's names for its references, as the history stores them.
RELEASE_TOOL_NAMES = {f"sweep-{fmt}": f"ref-{fmt}" for fmt in
                      ("rar14", "rar20", "rar29", "rar30", "rar40", "rar50", "rar70")}


def tools_from_meta(tools: dict) -> dict:
    out = {}
    for name, t in tools.items():
        entry = {k: t[k] for k in ("version", "sha256") if k in t}
        out[RELEASE_TOOL_NAMES.get(name, name)] = entry
    return out


def _op_metrics(r: dict) -> dict:
    wall, cpu = r.get("wall_min_s"), r.get("cpu_s_at_min")
    return {"wall_s": wall, "wall_median_s": r.get("wall_median_s"), "cpu_s": cpu,
            "cpu_per_wall": ratio(cpu, wall), "rss_kib": r.get("rss_max_kib"),
            "out_bytes": r.get("out_bytes"), "rounds": r.get("rounds"), "threads": r.get("threads"),
            "cpus": r.get("cpus"), "ok": r.get("ok")}


def group_base(name: str) -> str:
    """`text m3 t22` -> `text/m3`: the group's name without its thread count."""
    return "/".join(w for w in name.split() if not re.fullmatch(r"t\d+", w))


def timed_cells(result: dict) -> dict:
    """One cell per rars command in the timed scenarios, against the group's reference."""
    many = result["meta"].get("threads_available")
    cells = {}
    for sc in result["scenarios"]:
        for group in sc.get("groups", []):
            ref = next((r for r in group["results"] if r["reference"]), None)
            peers = {r["tool"]: _op_metrics(r) for r in group["results"]
                     if not r["tool"].startswith("rars") and not r["reference"]}
            for r in group["results"]:
                if not r["tool"].startswith("rars"):
                    continue
                threads = group["attrs"].get("threads", r.get("threads"))
                cid = f"{sc['name']}/{group_base(group['name'])}/{thread_label(threads, many)}/{r['tool']}"
                m = _op_metrics(r)
                rm = _op_metrics(ref) if ref else None
                cells[cid] = {
                    "ref": ref["tool"] if ref else None, "ok": bool(r["ok"]),
                    "metrics": m, "ref_metrics": rm, "peers": peers,
                    "ratios": {
                        "time": ratio(m["wall_s"], rm["wall_s"]) if rm and rm["ok"] else None,
                        "cpu": ratio(m["cpu_s"], rm["cpu_s"]) if rm and rm["ok"] else None,
                        "rss": ratio(m["rss_kib"], rm["rss_kib"]) if rm else None,
                        "size": ratio(m["out_bytes"], rm["out_bytes"]) if rm else None,
                    },
                }
    return cells


def size_cell_id(fmt: str, solid: bool, level: int, cls: str) -> str:
    return f"size/{fmt}/{'solid' if solid else 'plain'}/m{level}/{cls}"


def sweep_cells(sweep: dict) -> dict:
    cells = {}
    for c in sweep["cells"]:
        ok = c["rars_status"] == "ok" and c.get("unrar_exit") in (0, None)
        has_ref = c.get("ref_status") == "ok" and c.get("ref_packed")
        cells[size_cell_id(c["format"], c["solid"], c["level"], c["class"])] = {
            "ref": f"ref-{c['format']}" if c.get("ref_version") else None, "ok": ok,
            "metrics": {"input_bytes": c["input_bytes"], "packed": c["rars_packed"],
                        "archive": c["rars_archive"], "status": c["rars_status"], "unrar_exit": c.get("unrar_exit")},
            "ref_metrics": {"packed": c["ref_packed"], "archive": c["ref_archive"], "status": c["ref_status"]}
            if c.get("ref_status") else None,
            "ratios": {"size": ratio(c["rars_packed"], c["ref_packed"]) if has_ref else None},
        }
    return cells


def discmaster_cells(dm: dict) -> dict:
    cells = {}
    for fam, s in dm["families"].items():
        rars, unrar = s.get("rars"), s.get("unrar")
        if not rars:
            continue
        cells[f"discmaster/{fam}/rars"] = {
            "ref": "unrar" if unrar else None, "ok": not unrar or rars["pass"] >= unrar["pass"],
            "metrics": {"archives": s["archives"], "bytes": s["bytes"], **rars},
            "ref_metrics": unrar,
            "ratios": {"time": ratio(rars["wall_total_s"], unrar["wall_total_s"]) if unrar else None,
                       "rss": ratio(rars["rss_max_kib"], unrar["rss_max_kib"]) if unrar else None},
        }
    return cells


def from_release(result: dict, *, label: str, machine: dict, load: dict | None, note: str | None = None,
                 rars_extra: dict | None = None) -> dict:
    """A history record for one `rars-bench release` result."""
    meta = result["meta"]
    cells = timed_cells(result)
    errors = {}
    for sc in result["scenarios"]:
        if sc.get("error"):
            errors[sc["name"]] = sc["error"]
        if "sweep" in sc:
            cells.update(sweep_cells(sc["sweep"]))
        if "discmaster" in sc:
            cells.update(discmaster_cells(sc["discmaster"]))
    rars = meta["tools"]["rars"]
    return {
        "schema": SCHEMA,
        "tier": "timed" if meta["scale"] == "full" else f"timed-{meta['scale']}",
        "date": meta["date"],
        "elapsed_s": round(meta.get("elapsed_s", 0), 1),
        "rars": {"label": label, "version": rars.get("version"), "commit": rars.get("commit"),
                 "sha256": rars.get("sha256"), "note": note, **(rars_extra or {})},
        "machine": {**machine, "threads_available": meta.get("threads_available")},
        "load": load,
        "tools": tools_from_meta(meta["tools"]),
        "settings": {"scale": meta["scale"], "rounds": meta.get("rounds"),
                     "scale_params": meta.get("scale_params"), "scenarios": meta.get("scenarios_run")},
        "cells": cells,
        "checks": {"scenario_errors": errors},
    }
