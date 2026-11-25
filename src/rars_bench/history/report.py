"""`rars-bench history`: cells against versions, for one machine, in markdown.

Columns are versions in commit order; rows are cells. A size cell shows rars'
packed bytes and its ratio to the reference; a timed cell shows the time and
peak-RSS ratios to its reference. A value is bold when it moved since the
previous column: a size on any change, a ratio by more than the threshold
(5% by default), a verdict when it flipped. A ratio is only compared with one
taken against the same tool name; a dagger marks one whose reference tool
changed version or hash since the previous column.

Columns from another machine (`--other-machines`) fill in versions this
machine has no record for. They are marked ratio-only: raw bytes, seconds
and memory from another machine are not shown, only ratios.
"""
from __future__ import annotations

from ..registry import tool_changes
from .machine import load_warning


def order_key(record: dict) -> tuple:
    return (record["rars"].get("commit_date") or record["date"], record["date"])


def select(records: list[dict], tier: str, machine: str, others: bool) -> list[tuple[dict, bool]]:
    """(record, cross-machine) per version column, oldest first."""
    mine = [r for r in records if r["tier"] == tier and r["machine"]["id"] == machine]
    cols = [(r, False) for r in mine]
    if others:
        have = {r["rars"]["label"] for r in mine}
        latest: dict[str, dict] = {}
        for r in records:
            if r["tier"] == tier and r["machine"]["id"] != machine and r["rars"]["label"] not in have:
                if r["rars"]["label"] not in latest or r["date"] > latest[r["rars"]["label"]]["date"]:
                    latest[r["rars"]["label"]] = r
        cols += [(r, True) for r in latest.values()]
    return sorted(cols, key=lambda c: order_key(c[0]))


def default_machine(records: list[dict], here: str | None) -> str | None:
    ids = [r["machine"]["id"] for r in records]
    if here in ids:
        return here
    return max(set(ids), key=ids.count) if ids else None


def _pct(r: float | None) -> str:
    return "-" if r is None else f"{(r - 1) * 100:+.2f}%"


def _x(r: float | None) -> str:
    return "-" if r is None else f"{r:.2f}x"


def _bold(text: str, on: bool) -> str:
    return f"**{text}**" if on and text != "-" else text


def _moved(a: float | None, b: float | None, threshold: float) -> bool:
    if a is None or b is None:
        return False
    return abs(b / a - 1) > threshold if a else a != b


def render_cell(cell: dict | None, prev: dict | None, cross: bool, ref_changed: set[str],
                threshold: float) -> str:
    if cell is None:
        return ""
    same_ref = prev is not None and prev.get("ref") == cell.get("ref") and cell.get("ref") is not None
    ratios, pratios = cell.get("ratios") or {}, (prev or {}).get("ratios") or {}
    dagger = "†" if cell.get("ref") in ref_changed else ""
    parts = []
    if "packed" in (cell.get("metrics") or {}):
        packed = cell["metrics"].get("packed")
        size = ratios.get("size")
        size_moved = same_ref and _moved(pratios.get("size"), size, 0.0)
        if cross:
            parts.append(_bold(_pct(size), size_moved) + dagger)
        else:
            raw_moved = prev is not None and prev["metrics"].get("packed") != packed
            parts.append(_bold(f"{packed:,}" if packed else "-", raw_moved)
                         + (f" ({_bold(_pct(size), size_moved)}{dagger})" if size is not None else ""))
    else:
        for key, label in (("time", "t"), ("rss", "m")):
            value = ratios.get(key)
            if value is not None:
                parts.append(f"{label} " + _bold(_x(value), same_ref and _moved(pratios.get(key), value, threshold))
                             + dagger)
        if ratios.get("size") is not None:
            parts.append("s " + _bold(_pct(ratios["size"]), same_ref and _moved(pratios.get("size"), ratios["size"], 0.0)))
    if not cell.get("ok", True):
        parts.append(_bold("FAIL", True))
    elif prev is not None and not prev.get("ok", True):
        parts.append("**pass**")
    return " ".join(parts) or "-"


def render(records: list[dict], *, machine: str, tier: str, others: bool = False, threshold: float = 0.05,
           changed_only: bool = False) -> str:
    cols = select(records, tier, machine, others)
    out = [f"# rars history: {tier} tier, machine `{machine}`", ""]
    if not cols:
        return "\n".join(out + ["No records.", ""])
    out += [f"Bold: moved since the previous column (sizes on any change, ratios by more than {threshold:.0%}, "
            "verdicts when they flip). Ratios are rars over the reference: above 1 (or +) is slower, bigger "
            "or hungrier. † the reference tool changed since the previous column. `t` time ratio, `m` peak "
            "RSS ratio, `s` output size against the reference's.", ""]
    out += ["| version | rars | commit | run | machine | checks | notes |", "|---|---|---|---|---|---|---|"]
    changed_refs: list[set[str]] = []
    prev_tools = None
    for record, cross in cols:
        tools = {k: v for k, v in record["tools"].items() if k != "rars"}
        changes = tool_changes(prev_tools, tools) if prev_tools is not None else []
        changed_refs.append({line.split(":", 1)[0] for line in changes})
        prev_tools = tools
        notes = [f"tool {c}" for c in changes]
        load = record.get("load") or {}
        cpus = record["machine"].get("logical_cpus") or 1
        if tier != "quick":  # the quick tier times nothing, so load cannot skew it
            notes += [f"busy at {w}: {msg}" for w in ("start", "end")
                      if (msg := load_warning(load.get(w), cpus, use_loadavg=w == "start"))]
        if record["rars"].get("note"):
            notes.append(record["rars"]["note"])
        checks = record.get("checks") or {}
        counts = checks.get("counts")
        failed = sum(1 for c in record["cells"].values() if not c.get("ok", True))
        summary = (", ".join(f"{k} {v}" for k, v in counts.items()) if counts else f"{failed} failed cells")
        if checks.get("scenario_errors"):
            summary += f", {len(checks['scenario_errors'])} scenario errors"
        out.append(f"| {record['rars']['label']} | {record['rars'].get('version') or '-'} "
                   f"| `{(record['rars'].get('commit') or '?')[:12]}` | {record['date'][:10]} "
                   f"| {'other: `' + record['machine']['id'] + '`, ratio-only' if cross else 'this'} "
                   f"| {summary} | {'; '.join(notes).replace('|', '/')} |")
    out.append("")

    ids = sorted({cid for record, _ in cols for cid in record["cells"]})
    sections: dict[str, list[str]] = {}
    for cid in ids:
        head = cid.split("/")
        sections.setdefault("/".join(head[:2]) if head[0] == "size" else head[0], []).append(cid)
    headers = [f"{r['rars']['label']}{' (ratio-only)' if cross else ''}" for r, cross in cols]
    for section, cell_ids in sections.items():
        rows = []
        for cid in cell_ids:
            texts, prev = [], None
            for i, (record, cross) in enumerate(cols):
                cell = record["cells"].get(cid)
                texts.append(render_cell(cell, prev, cross, changed_refs[i], threshold))
                if cell is not None:
                    prev = cell
            if changed_only and not any("**" in t for t in texts):
                continue
            name = cid[len(section) + 1:] if cid.startswith(section + "/") else cid
            rows.append(f"| {name} | " + " | ".join(texts) + " |")
        if rows:
            out += [f"## {section}", "", "| cell | " + " | ".join(headers) + " |",
                    "|---|" + "---|" * len(headers)] + rows + [""]
    return "\n".join(out) + "\n"
