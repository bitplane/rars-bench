"""Markdown for one release-bench result, and for the difference between two."""
from __future__ import annotations

import statistics
from pathlib import Path


def mib(kib: int | None) -> str:
    return "-" if kib is None else f"{kib / 1024:.0f}"


def secs(s: float | None) -> str:
    if s is None:
        return "-"
    return f"{s:.3f}" if s < 10 else f"{s:.1f}"


def times(value: float | None) -> str:
    return f"{value:.2f}x" if value else "-"


def pct(gap: float | None) -> str:
    return "-" if gap is None else f"{gap * 100:+.2f}%"


def _ref(group: dict) -> dict | None:
    return next((r for r in group["results"] if r["reference"]), None)


def headline_rows(result: dict) -> list[dict]:
    """Each rars command against the group's native reference, in one flat list."""
    rows = []
    for sc in result["scenarios"]:
        for group in sc.get("groups", []):
            ref = _ref(group)
            fast = next((r for r in group["results"] if r["tool"] == "rarfast"), None)
            rs = next((r for r in group["results"] if r["tool"] == "rar-rs"), None)
            for r in group["results"]:
                if not r["tool"].startswith("rars"):
                    continue
                rows.append({"scenario": sc["name"], "group": group["name"], "attrs": group["attrs"],
                             "rars": r, "ref": ref, "rarfast": fast, "rar-rs": rs,
                             "time": r["wall_min_s"] / ref["wall_min_s"] if ref and ref["ok"] else None,
                             "rss": r["rss_max_kib"] / ref["rss_max_kib"] if ref and ref["rss_max_kib"] else None,
                             "size": (r["out_bytes"] / ref["out_bytes"] - 1)
                             if ref and r["out_bytes"] and ref["out_bytes"] else None})
    return rows


def render(result: dict) -> str:
    meta = result["meta"]
    out = [f"# rars release bench, {meta['date']}", ""]
    rars = meta["tools"]["rars"]
    out += [f"rars `{rars.get('version')}`, commit `{rars.get('commit', '?')[:12]}`"
            f"{' (dirty)' if rars.get('dirty') else ''} {rars.get('subject', '')}, binary sha256 "
            f"`{rars['sha256'][:16]}`. Scale `{meta['scale']}`, {meta['rounds']} rounds, "
            f"{meta['threads_available']} threads available, total {meta['elapsed_s'] / 60:.1f} min.", ""]
    out += host_lines(meta)
    out += ["| tool | version | sha256 |", "|---|---|---|"]
    for name, t in meta["tools"].items():
        out.append(f"| {name} | {t.get('version', '-')} | `{t.get('sha256', '-')[:16]}` |")
    out += ["", "Timings compare native binaries only. Times are the minimum over the rounds, "
            "interleaved; peak RSS is the largest. A ratio above 1 means rars is slower, bigger or "
            "uses more memory.", ""]

    out += ["## Headline: rars against the native reference", "",
            "| scenario | cell | rars | reference | rars s | ref s | time | rarfast s | rar-rs s | rars MiB "
            "| ref MiB | memory | size | rars ok |",
            "|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|"]
    for row in headline_rows(result):
        r, ref, fast, rs = row["rars"], row["ref"], row["rarfast"], row["rar-rs"]
        out.append(f"| {row['scenario']} | {row['group']} | {r['tool']} | {ref['tool'] if ref else '-'} "
                   f"| {secs(r['wall_min_s'])} | {secs(ref['wall_min_s']) if ref else '-'} "
                   f"| {times(row['time'])} "
                   f"| {secs(fast['wall_min_s']) if fast and fast['ok'] else ('fail' if fast else '-')} "
                   f"| {secs(rs['wall_min_s']) if rs and rs['ok'] else ('fail' if rs else '-')} "
                   f"| {mib(r['rss_max_kib'])} | {mib(ref['rss_max_kib']) if ref else '-'} "
                   f"| {times(row['rss'])} | {pct(row['size'])} "
                   f"| {'yes' if r['ok'] else '**NO**'} |")
    out.append("")
    out += medians(result)

    for sc in result["scenarios"]:
        out += [f"## {sc['name']}", "", sc["doc"], ""]
        if sc.get("error"):
            out += [f"**Scenario failed:** {sc['error']}", ""]
            continue
        if "groups" in sc:
            out += ["| cell | tool | threads | min s | median s | CPU s | peak MiB | output bytes | ok | detail |",
                    "|---|---|---:|---:|---:|---:|---:|---:|---|---|"]
            for group in sc["groups"]:
                for r in group["results"]:
                    detail = r["detail"].replace("|", "/").replace("\n", " ")[:120]
                    out.append(f"| {group['name']} | {r['tool']} | {r['threads'] or 'default'} "
                               f"| {secs(r['wall_min_s'])} | {secs(r['wall_median_s'])} | {secs(r['cpu_s_at_min'])} "
                               f"| {mib(r['rss_max_kib'])} | {r['out_bytes'] or '-'} "
                               f"| {'yes' if r['ok'] else '**NO**'} | {detail} |")
            out.append("")
        if "discmaster" in sc:
            out += discmaster_md(sc["discmaster"])
        if "sweep" in sc:
            out += sweep_md(sc["sweep"], result.get("cells_tsv"))
    return "\n".join(out) + "\n"


def host_lines(meta: dict) -> list[str]:
    """The CPU sets each thread label ran on, and a warning when the machine was busy."""
    from ..history.machine import load_warning
    machine, load = meta.get("machine"), meta.get("load") or {}
    if not machine:
        return []
    sets = machine.get("cpu_sets") or {}
    out = [f"Machine `{machine['id']}`, {machine.get('cpu_model')}, {machine.get('logical_cpus')} logical CPUs; "
           + ", ".join(f"{k} on CPUs {v}" for k, v in sets.items()) + ".", ""]
    for when in ("start", "end"):
        warning = load_warning(load.get(when), machine.get("logical_cpus") or 1, use_loadavg=when == "start")
        if warning:
            out += [f"**Busy machine at the {when}:** {warning}. Timings may be inflated.", ""]
    return out


def medians(result: dict) -> list[str]:
    """Per scenario, level and thread count: the median class beside the byte-weighted total."""
    groups: dict[tuple, list] = {}
    for row in headline_rows(result):
        attrs = row["attrs"]
        if "class" not in attrs or row["rars"]["tool"] != "rars" or row["time"] is None:
            continue
        groups.setdefault((row["scenario"], attrs.get("level"), attrs.get("threads")), []).append(row)
    if not groups:
        return []
    out = ["### Create cells by level and threads: median class and byte-weighted total", "",
           "| scenario | level | threads | classes | median time | median size | total size |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for (scenario, level, threads), rows in sorted(groups.items(), key=lambda kv: tuple(map(str, kv[0]))):
        sized = [r for r in rows if r["size"] is not None]
        total = (sum(r["rars"]["out_bytes"] for r in sized) / sum(r["ref"]["out_bytes"] for r in sized) - 1) \
            if sized else None
        out.append(f"| {scenario} | {level} | {threads} | {len(rows)} "
                   f"| {statistics.median(r['time'] for r in rows):.2f}x "
                   f"| {pct(statistics.median(r['size'] for r in sized)) if sized else '-'} | {pct(total)} |")
    return out + [""]


def discmaster_md(dm: dict) -> list[str]:
    tools = [t for t in ("rars", "unrar", "rarfast", "rar-rs") if t in dm["families"]["all"]]
    head = "| family | archives | MB | " + " | ".join(f"{t} pass | {t} s | {t} median ms | {t} peak MiB"
                                                    for t in tools) + " |"
    out = [head, "|---|---:|---:|" + "---:|" * (4 * len(tools))]
    for fam, s in dm["families"].items():
        cells = []
        for t in tools:
            x = s[t]
            cells += [str(x["pass"]), secs(x["wall_total_s"]),
                      f"{x['wall_median_s'] * 1000:.1f}" if x["wall_median_s"] is not None else "-",
                      mib(x["rss_max_kib"])]
        out.append(f"| {fam} | {s['archives']} | {s['bytes'] / 1e6:.1f} | " + " | ".join(cells) + " |")
    out.append("")
    bad = dm["rars_fails_where_unrar_passes"]
    out.append(f"rars fails {len(bad)} archive(s) that unrar passes; unrar itself now fails "
               f"{len(dm['unrar_fails_now'])} of the manifest's `ok` rows. Paths are in the JSON.")
    return out + [""]


def sweep_md(sw: dict, tsv: str | None) -> list[str]:
    out = ["| reference | format | variants | classes | rars tested by |", "|---|---|---|---:|---|"]
    for p in sw["phases"]:
        out.append(f"| {p['reference']} | {p['format']} | {p['variants']} | {len(p['classes'])} "
                   f"| {p['rars_tested_by']} |")
    out += ["", "Packed bytes, rars over the reference. `bytes` is the byte-weighted total, "
            "which the large incompressible class dominates; read the medians.", "",
            "| format | solid | level | cells | bytes | median | median small | median large | worst class | worst |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|---:|"]
    for r in sw["rollup"]:
        out.append(f"| {r['format']} | {'solid' if r['solid'] else ''} | {r['level']} | {r['cells']} "
                   f"| {pct(r['gap_bytes'])} | {pct(r['median_gap'])} | {pct(r['median_gap_small'])} "
                   f"| {pct(r['median_gap_large'])} | {r['worst_class']} | {pct(r['worst_gap'])} |")
    out.append("")
    if sw["failures"]:
        out += ["Cells where rars' archive failed (sweep status, or unrar's own test):", "",
                "| format | solid | level | class | status | unrar exit |", "|---|---|---:|---|---|---:|"]
        for c in sw["failures"]:
            out.append(f"| {c['format']} | {c['solid']} | {c['level']} | {c['class']} | {c['rars_status']} "
                       f"| {c['unrar_exit']} |")
        out.append("")
    unscored = sum(1 for c in sw["cells"] if c["gap"] is None)
    out.append(f"{len(sw['cells'])} cells, {unscored} unscored (no reference, or the reference's own round "
               f"trip failed). Every cell, worst gap first: `{tsv}`.")
    return out + [""]


# --- comparing two runs ------------------------------------------------------------------------

def compare(old: dict, new: dict, threshold: float = 0.05) -> str:
    """Old against new; cells that moved past the threshold are bold."""
    def mark(text: str, moved: bool) -> str:
        return f"**{text}**" if moved else text

    def label(m: dict) -> str:
        t = m["tools"]["rars"]
        return f"{t.get('version')} {t.get('commit', '?')[:12]} ({m['date']})"

    out = [f"# Release bench: {label(old['meta'])} -> {label(new['meta'])}", ""]
    for side, m in (("old", old["meta"]), ("new", new["meta"])):
        refs = ", ".join(f"{n} {t.get('version', '-')}" for n, t in m["tools"].items() if n != "rars")
        out.append(f"- {side}: scale {m['scale']}, {m['rounds']} rounds; {refs}")
    from ..history.store import tools_from_meta
    from ..registry import tool_changes
    changed = tool_changes(*(tools_from_meta({k: v for k, v in m["tools"].items() if k != "rars"})
                             for m in (old["meta"], new["meta"])))
    if changed:
        out += ["", "**Tools changed between the runs** (ratios against them may not line up):", ""]
        out += [f"- {line}" for line in changed]
    out += ["", f"Bold: moved by more than {threshold:.0%} (time, memory) or 0.1 points (size).", "",
            "| scenario | cell | tool | old s | new s | change | old MiB | new MiB | old bytes | new bytes | ok |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]

    def index(result: dict) -> dict:
        return {(sc["name"], g["name"], r["tool"]): r for sc in result["scenarios"]
                for g in sc.get("groups", []) for r in g["results"]}
    a, b = index(old), index(new)
    for key in [k for k in b if k in a]:
        x, y = a[key], b[key]
        dt = y["wall_min_s"] / x["wall_min_s"] - 1 if x["wall_min_s"] else 0
        dm = (y["rss_max_kib"] / x["rss_max_kib"] - 1) if x["rss_max_kib"] else 0
        ds = (y["out_bytes"] / x["out_bytes"] - 1) if x["out_bytes"] and y["out_bytes"] else 0
        out.append(f"| {key[0]} | {key[1]} | {key[2]} | {secs(x['wall_min_s'])} | {secs(y['wall_min_s'])} "
                   f"| {mark(f'{dt * 100:+.1f}%', abs(dt) > threshold)} | {mib(x['rss_max_kib'])} "
                   f"| {mark(mib(y['rss_max_kib']), abs(dm) > threshold)} | {x['out_bytes'] or '-'} "
                   f"| {mark(str(y['out_bytes'] or '-'), abs(ds) > 0.001)} "
                   f"| {mark(('yes' if y['ok'] else 'NO'), x['ok'] != y['ok'])} |")
    only = sorted(set(a) ^ set(b))
    if only:
        out += ["", f"{len(only)} cell(s) in only one run, e.g. {only[0]}."]

    def sweep_of(result: dict) -> dict | None:
        return next((sc["sweep"] for sc in result["scenarios"] if "sweep" in sc), None)
    sa, sb = sweep_of(old), sweep_of(new)
    if sa and sb:
        out += ["", "## Format sweep", "",
                "| format | solid | level | old median | new median | old bytes | new bytes |",
                "|---|---|---:|---:|---:|---:|---:|"]
        ra = {(r["format"], r["solid"], r["level"]): r for r in sa["rollup"]}
        for r in sb["rollup"]:
            o = ra.get((r["format"], r["solid"], r["level"]))
            if o is None:
                continue
            moved = abs(r["median_gap"] - o["median_gap"]) > 0.001
            out.append(f"| {r['format']} | {'solid' if r['solid'] else ''} | {r['level']} "
                       f"| {pct(o['median_gap'])} | {mark(pct(r['median_gap']), moved)} | {pct(o['gap_bytes'])} "
                       f"| {mark(pct(r['gap_bytes']), abs(r['gap_bytes'] - o['gap_bytes']) > 0.001)} |")
        ca = {(c["format"], c["solid"], c["level"], c["class"]): c for c in sa["cells"]}
        flips = [(k, ca[k]["rars_status"], c["rars_status"]) for c in sb["cells"]
                 if (k := (c["format"], c["solid"], c["level"], c["class"])) in ca
                 and ca[k]["rars_status"] != c["rars_status"]]
        for k, was, now in flips:
            out.append(f"- **status** {k}: {was} -> {now}")

    def dm_of(result: dict) -> dict | None:
        return next((sc["discmaster"] for sc in result["scenarios"] if "discmaster" in sc), None)
    da, db = dm_of(old), dm_of(new)
    if da and db:
        out += ["", "## DiscMaster sample", "", "| family | old rars pass | new rars pass | old rars s | new rars s |",
                "|---|---:|---:|---:|---:|"]
        for fam, s in db["families"].items():
            o = da["families"].get(fam)
            if o:
                out.append(f"| {fam} | {o['rars']['pass']}/{o['archives']} "
                           f"| {mark(str(s['rars']['pass']) + '/' + str(s['archives']), o['rars']['pass'] != s['rars']['pass'])} "
                           f"| {secs(o['rars']['wall_total_s'])} | {secs(s['rars']['wall_total_s'])} |")
    return "\n".join(out) + "\n"


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path
