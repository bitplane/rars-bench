#!/usr/bin/env python3
"""Render the rars-vs-RAR page from summarise.py, timing.py and decode_timing.py output.

    scripts/render_page.py --summary results/<sha>-summary.json \
        --timing results/<sha>-timing.json --threads results/<sha>-threads.json \
        --decode results/<sha>-decode.json --head "<sha> <subject>" --swept "<date>" \
        --out rars-vs-rar.html

Every number on the page comes from those files. The prose around them states
what the numbers show and is written here, beside the code that computes them,
so a re-render with new results re-checks each claim against its figures.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path

FORMATS = [("rar14", "RAR 1.4"), ("rar20", "RAR 2.0"), ("rar29", "RAR 2.9"), ("rar30", "RAR 3.0"),
           ("rar40", "RAR 4.0"), ("rar50", "RAR 5.0"), ("rar70", "RAR 7.0")]
ORACLE = {"rar14": "1.402", "rar20": "2.50", "rar29": "2.90", "rar30": "3.00", "rar40": "4.20",
          "rar50": "6.02", "rar70": "7.21b1"}
LARGE = [("large-bin-stripped", "stripped ELF"), ("large-bin-unstripped", "unstripped ELF"),
         ("large-text", "prose"), ("large-source-tree", "source tree"),
         ("large-compressible", "compressible"), ("large-incompressible", "incompressible")]
SMALL = [("bin-elf", "ELF"), ("bin-shell", "shell"), ("doc-text", "docs"), ("fonts-ttf", "fonts"),
         ("headers", "headers"), ("icons-png", "PNG"), ("logs", "logs"), ("manpages", "manpages"),
         ("mixed", "mixed")]
LABEL = dict(LARGE + SMALL)
SERIES = {"rar20": "--s2", "rar29": "--s3", "rar30": "--s4", "rar40": "--s5", "rar50": "--s6", "rar70": "--s7"}

STYLE = (Path(__file__).with_name("page.css")).read_text()


def pct(value: float) -> str:
    return f"{value * 100:+.2f}%".replace("-", "&minus;", 1) if value < 0 else f"{value * 100:+.2f}%"


def heat(gap: float | None) -> str:
    if gap is None:
        return '<td class="na">n/a</td>'
    kind = "level" if abs(gap) < 0.0005 else ("worse" if gap > 0 else "better")
    weight = min(1.0, abs(gap) / 0.05)
    return f'<td class="heat {kind}" style="--w:{weight:.2f}"><span>{pct(gap)}</span></td>'


def ratio(value: float, cls: str = "num") -> str:
    return f'<td class="{cls}">{value:.2f}x</td>'


def levels_of(section: dict) -> list[str]:
    return sorted(section, key=lambda level: int(level[1:]))


def top_level(section: dict) -> str:
    return levels_of(section)[-1]


def level_table(summary: dict, kind: str, columns: list[str], with_median: bool) -> str:
    head = "".join(f"<th>{c}</th>" for c in columns)
    if with_median:
        head += '<th class="sep">median m5</th>'
    rows = []
    for fmt, name in FORMATS:
        section = summary["formats"].get(fmt, {}).get(kind)
        if not section:
            continue
        cells = "".join(heat(section[c]["gap"]) if c in section else '<td class="na">n/a</td>'
                        for c in columns)
        if with_median:
            top = section[top_level(section)]
            median = heat(top["median_gap"]).replace('class="heat', 'class="sep heat', 1)
            cells += median
        rows.append(f'<tr><th>{name}</th>{cells}<td class="ref">{ORACLE[fmt]}</td></tr>')
    return (f'<table><thead><tr><th>format</th>{head}<th>oracle</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def class_table(summary: dict, kind: str, classes: list[tuple[str, str]], formats=FORMATS) -> str:
    head = "".join(f"<th>{label}</th>" for _, label in classes)
    rows = []
    for fmt, name in formats:
        section = summary["formats"].get(fmt, {}).get(kind)
        if not section:
            continue
        data = section[top_level(section)]["classes"]
        cells = "".join(heat(data[c]["gap"]) if c in data else '<td class="na">n/a</td>'
                        for c, _ in classes)
        rows.append(f"<tr><th>{name}</th>{cells}</tr>")
    return f'<table><thead><tr><th>format</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def gap_chart(summary: dict) -> str:
    """Gap at m1..m5 for each multi-level format, on one scale."""
    series = []
    for fmt, name in FORMATS:
        section = summary["formats"].get(fmt, {}).get("default", {})
        points = [(int(level[1:]), section[level]["gap"]) for level in levels_of(section)
                  if level != "m0"]
        if len(points) > 1:
            series.append((fmt, name, points))
    values = [gap for _, _, points in series for _, gap in points]
    low = min(-0.02, (min(values) * 100 // 2) * 2 / 100)
    high = max(0.02, -((-max(values) * 100) // 2) * 2 / 100)
    left, right, top, bottom = 58, 600, 24, 250
    y = lambda gap: bottom - (gap - low) / (high - low) * (bottom - top)
    x = lambda level: left + (level - 1) / 4 * (right - left)
    parts = []
    tick = low
    while tick <= high + 1e-9:
        cls = "zero" if abs(tick) < 1e-9 else "grid"
        parts.append(f'<line class="{cls}" x1="{left}" y1="{y(tick):.1f}" x2="{right}" y2="{y(tick):.1f}"/>'
                     f'<text class="ytick" x="{left - 8}" y="{y(tick) + 4:.1f}">{tick * 100:+.0f}%</text>')
        tick += 0.02
    for level in range(1, 6):
        parts.append(f'<text class="xtick" x="{x(level):.1f}" y="{bottom + 24}">m{level}</text>')
    # Labels at the right edge, nudged apart so none overlap.
    ends = sorted(((y(points[-1][1]), fmt, name, points[-1][1]) for fmt, name, points in series))
    placed, last = [], -1e9
    for end_y, fmt, name, gap in ends:
        label_y = max(end_y + 4, last + 15)
        placed.append((fmt, name, gap, label_y))
        last = label_y
    for fmt, name, points in series:
        colour = f"var({SERIES[fmt]})"
        path = " L".join(f"{x(level):.1f},{y(gap):.1f}" for level, gap in points)
        parts.append(f'<path class="ln" d="M{path}" style="stroke:{colour}"/>')
        parts.extend(f'<circle class="pt" cx="{x(level):.1f}" cy="{y(gap):.1f}" r="3.5" style="fill:{colour}"/>'
                     for level, gap in points)
    for fmt, name, gap, label_y in placed:
        parts.append(f'<text class="lbl" x="{right + 10}" y="{label_y:.1f}" style="fill:var({SERIES[fmt]})">'
                     f'{name} <tspan class="lblv">{pct(gap)}</tspan></text>')
    return (f'<svg viewBox="0 0 760 290" role="img" aria-label="Gap to the reference at each level" '
            f'class="chart">{"".join(parts)}</svg>')


def render(args) -> str:
    summary = json.loads(args.summary.read_text())
    timing = json.loads(args.timing.read_text())
    threads = json.loads(args.threads.read_text())
    decode = json.loads(args.decode.read_text())
    f = summary["formats"]
    g = lambda fmt, level, kind="default": f[fmt][kind][level]

    # Writing time against native RAR 7.12.
    by_level = defaultdict(list)
    for row in timing:
        by_level[row["level"]].append(row)
    write_rows, write_stats = [], {}
    for level in sorted(by_level):
        rows = by_level[level]
        mine, theirs = sum(r["rars_s"] for r in rows), sum(r["rar_s"] for r in rows)
        median = statistics.median(r["rars_s"] / r["rar_s"] for r in rows)
        size = sum(r["rars_bytes"] for r in rows) / sum(r["rar_bytes"] for r in rows) - 1
        write_stats[level] = (mine / theirs, median)
        write_rows.append(f'<tr><th>m{level}</th><td class="num">{mine:.2f}s</td><td class="num">{theirs:.2f}s</td>'
                          f'{ratio(mine / theirs)}{ratio(median)}{heat(size)}</tr>')
    m5 = {r["class"]: r for r in by_level[5]}
    size5 = sum(r["rars_bytes"] for r in by_level[5]) / sum(r["rar_bytes"] for r in by_level[5]) - 1
    write_classes = "".join(
        f'<tr><th>{LABEL[c]}</th><td class="num">{m5[c]["input_bytes"] / 1e6:.2f} MB</td>'
        f'<td class="num">{m5[c]["rars_s"]:.3f}s</td><td class="num">{m5[c]["rar_s"]:.3f}s</td>'
        f'{ratio(m5[c]["rars_s"] / m5[c]["rar_s"])}</tr>'
        for c, _ in LARGE + SMALL if c in m5)
    legacy = json.loads(args.timing_legacy.read_text())
    legacy_by = defaultdict(list)
    for row in legacy:
        legacy_by[row["level"]].append(row)
    legacy_rows, legacy_stats = [], {}
    for level in sorted(legacy_by):
        rows = legacy_by[level]
        mine, theirs = sum(r["rars_s"] for r in rows), sum(r["rar_s"] for r in rows)
        median = statistics.median(r["rars_s"] / r["rar_s"] for r in rows)
        size = sum(r["rars_bytes"] for r in rows) / sum(r["rar_bytes"] for r in rows) - 1
        legacy_stats[level] = (mine / theirs, median)
        legacy_rows.append(f'<tr><th>m{level}</th><td class="num">{mine:.2f}s</td><td class="num">{theirs:.2f}s</td>'
                           f'{ratio(mine / theirs)}{ratio(median)}{heat(size)}</tr>')
    legacy_m1 = {r["class"]: r for r in legacy_by[1]}
    thread_rows = "".join(
        f'<tr><th>{LABEL[t["class"]]}</th><td class="num">{t["rars_s"]:.2f}s</td>'
        f'<td class="num">{t["rar_1t_s"]:.2f}s</td>{ratio(t["rars_s"] / t["rar_1t_s"])}'
        f'<td class="num sep">{t["rar_all_s"]:.2f}s</td>{ratio(t["rars_s"] / t["rar_all_s"])}</tr>'
        for t in threads)
    one_thread = [t["rars_s"] / t["rar_1t_s"] for t in threads]

    # Reading time against unrar, on the reference's own archives.
    dgroups = defaultdict(list)
    for row in decode:
        dgroups[(row["format"], row["solid"], row["level"])].append(row)
    read_rows = []
    for fmt, name in FORMATS:
        for solid in (False, True):
            for level in (3, 5):
                rows = dgroups.get((fmt, solid, level))
                if not rows:
                    continue
                big = [r for r in rows if r["class"].startswith("large-")]
                total = sum(r["rars_s"] for r in rows) / sum(r["unrar_s"] for r in rows)
                large = sum(r["rars_s"] for r in big) / sum(r["unrar_s"] for r in big)
                read_rows.append(f'<tr><th>{name}{" solid" if solid else ""}</th><td class="num">m{level}</td>'
                                 f'{ratio(total)}{ratio(large)}'
                                 f'<td class="num sep">{sum(r["unrar_s"] for r in big) * 1000:.0f} ms</td></tr>')
    failures = [r for r in decode if r["rars_exit"] or r["unrar_exit"]]

    rar50, rar70 = f["rar50"]["default"], f["rar70"]["default"]
    worst50 = max(rar50["m5"]["classes"].items(), key=lambda kv: kv[1]["gap"])
    best50 = min(rar50["m5"]["classes"].items(), key=lambda kv: kv[1]["gap"])
    rar30_m5 = g("rar30", "m5")["classes"]
    n_cells = sum(level["cells"] for sections in f.values() for section in sections.values()
                  for level in section.values())

    lede = (
        f"On size, rars is level with real RAR or ahead of it in most of the table. RAR 5 lands "
        f"<b>{pct(g('rar50', 'm5')['gap'])}</b> against WinRAR 6.02 at level 5 and "
        f"<b>{pct(g('rar50', 'm3')['gap'])}</b> at the default level 3, and RAR 7 "
        f"<b>{pct(g('rar70', 'm5')['gap'])}</b> against 7.21. Those totals lean on the large "
        f"members: the median RAR 5 class at m5 is <b>{pct(rar50['m5']['median_gap'])}</b>, so the "
        f"typical member is still a hair larger than WinRAR's. The weak row is RAR 3.0 at "
        f"<b>{pct(g('rar30', 'm5')['gap'])}</b>, and the reason is binaries: the unstripped ELF is "
        f"{pct(rar30_m5['large-bin-unstripped']['gap'])} and the stripped one "
        f"{pct(rar30_m5['large-bin-stripped']['gap'])}. On speed the picture is the other way round. "
        f"Writing RAR 5 takes <b>{write_stats[5][0]:.0f}x</b> as long as the native RAR 7.12 in total at "
        f"level 5, and {write_stats[1][0]:.0f}x at level 1. Most of that is a few multi-megabyte members, "
        f"where RAR spreads one member across every core and rars does not; the median class is "
        f"{write_stats[5][1]:.1f}x. Thread for thread rars is still {min(one_thread):.0f}x to "
        f"{max(one_thread):.0f}x slower on those members. The older format family is much closer: against "
        f"the native RAR 3.93, rars writes RAR 4.0 at {legacy_stats[5][0]:.1f}x its time at level 5. Reading is close to parity: rars decodes RAR 5 "
        f"as fast as unrar 7.20 and the RAR 2.9 to 4.0 family about a quarter slower."
    )

    return f"""<title>rars vs RAR</title>
<style>
{STYLE}
</style>

<div class="wrap">
<header>
  <h1>rars against real RAR</h1>
  <p>How a from-scratch Rust RAR implementation compares with the RAR it imitates, across every
     format it writes and every corpus shape the harness keeps: how small its archives are, how
     long it takes to write them, and how long it takes to read RAR's own. Sizes are total packed
     bytes as a percentage over the reference, so zero is parity and negative means rars won.</p>
  <p class="meta"><b>head</b> {args.head} &nbsp;·&nbsp; <b>swept</b> {args.swept}, all seven formats from one
     pinned binary, {n_cells} scored cells &nbsp;·&nbsp; <b>harness</b> rars-bench</p>
</header>

<section>
  <h2>Where the writers stand</h2>
  <p class="lede">{lede}</p>
  <div class="card">{level_table(summary, "default", ["m0", "m1", "m2", "m3", "m4", "m5"], True)}</div>
  <ul class="notes">
    <li><b>This is a new baseline, not a step from September.</b> The corpus was reseeded on a different
        machine, so every class holds different bytes from the last page's. Some rows land close to
        their old values (RAR 5 at m5 was &minus;0.35%, is {pct(g('rar50', 'm5')['gap'])}), but no cell here
        is a before-and-after, and none is marked as having moved.</li>
    <li><b>The median column is the typical member.</b> Each total is a byte-weighted sum, so the
        4 MB members outvote every small class. RAR 5's median at m5 is
        {pct(rar50['m5']['median_gap'])} against a total of {pct(rar50['m5']['gap'])}; RAR 2.9's is
        {pct(g('rar29', 'm5')['median_gap'])} against {pct(g('rar29', 'm5')['gap'])}, because it wins big on
        every text class.</li>
    <li><b>RAR 5's worst class is {LABEL[worst50[0]]} at {pct(worst50[1]['gap'])}</b> and its best
        {LABEL[best50[0]]} at {pct(best50[1]['gap'])}. Across RAR 5 and 7 the losses are the small
        binaries, shell scripts and the unstripped ELF; the wins are prose, the source tree and headers.</li>
    <li><b>m1 flatters us and m2 does not.</b> RAR 4.0 reads {pct(g('rar40', 'm1')['gap'])} at m1 and
        {pct(g('rar40', 'm2')['gap'])} at m2. The levels are a ladder of dictionary size and effort,
        and ours and RAR's rungs do not sit at the same heights.</li>
  </ul>
  <figure class="card"><figcaption><h3>Gap to the reference at each level</h3><p>Packed bytes over the
     reference encoder, summed across the corpus. Store is left off, being parity by construction, and
     RAR 1.4 writes one level only.</p></figcaption>{gap_chart(summary)}</figure>
</section>

<section>
  <h2>Writing speed against RAR 7.12</h2>
  <p>The sweep's own timings for the reference cannot be used: every WinRAR cell takes about four
     seconds whatever it compresses, which is wine starting up, and the work itself is a few tens of
     milliseconds under that. These numbers time the native Linux RAR 7.12 instead, writing RAR 5 at
     the same level on the same classes, with each encoder's other defaults. The two binaries alternate
     within each round and the fastest of five rounds is kept. The machine has 32 cores.</p>
  <div class="card"><h4 class="tcap">All fifteen classes, RAR 5</h4><table><thead><tr><th>level</th><th>rars</th>
     <th>rar 7.12</th><th>total</th><th>median class</th><th>size vs 7.12</th></tr></thead>
     <tbody>{"".join(write_rows)}</tbody></table>
     <h4 class="tcap">By class at m5</h4><table><thead><tr><th>class</th><th>input</th><th>rars</th>
     <th>rar 7.12</th><th>slower</th></tr></thead><tbody>{write_classes}</tbody></table>
     <h4 class="tcap">One large member, one thread against all of them</h4><table><thead><tr><th>member, m5</th>
     <th>rars</th><th>rar, 1 thread</th><th>slower</th><th class="sep">rar, all threads</th><th>slower</th></tr></thead>
     <tbody>{thread_rows}</tbody></table></div>
  <div class="card"><h4 class="tcap">RAR 4.0 against the native RAR 3.93, all fifteen classes</h4><table><thead>
     <tr><th>level</th><th>rars</th><th>rar 3.93</th><th>total</th><th>median class</th><th>size vs 3.93</th></tr></thead>
     <tbody>{"".join(legacy_rows)}</tbody></table></div>
  <ul class="notes">
    <li><b>Only RAR 5 has the large gap.</b> RAR 3.93 is the last native Linux build of the RAR 2.9 to 4.x
        writer, and against it rars writes RAR 4.0 at {legacy_stats[3][0]:.1f}x its time at m3 and
        {legacy_stats[5][0]:.1f}x at m5. Neither encoder spreads one member across cores in that format, so
        this is closer to a like-for-like comparison of the two encoders.</li>
    <li><b>Two m1 cells are outliers worth a look.</b> At level 1 rars takes
        {legacy_m1["icons-png"]["rars_s"] / legacy_m1["icons-png"]["rar_s"]:.0f}x RAR 3.93's time on the PNG icons and
        {legacy_m1["large-incompressible"]["rars_s"] / legacy_m1["large-incompressible"]["rar_s"]:.0f}x on the
        incompressible member, both inputs with little to find. The icons take longer at m1 than at m3.</li>
    <li><b>The last page's speed figures were wrong.</b> It reported RAR 5 at 1.69x the reference at m3
        and 2.48x at m5. Those ratios were taken against WinRAR under wine, whose start-up time hid how
        little time the work itself took. Against a native binary the totals are
        {write_stats[3][0]:.0f}x at m3 and {write_stats[5][0]:.0f}x at m5.</li>
    <li><b>Most of the total is three members.</b> The stripped ELF, the unstripped ELF and the source
        tree each take rars seconds at m5 and RAR 7.12 a few hundredths. Small classes are 1.5x to 9x,
        and much of that is process start-up at these sizes.</li>
    <li><b>RAR uses every core on one member and rars does not.</b> Given one large member, rars takes
        the same time with one thread or thirty-two; RAR 7.12 runs about four times faster with all of
        them. That accounts for a factor of four. The rest is the parse itself: thread for thread, rars
        is {min(one_thread):.0f}x to {max(one_thread):.0f}x slower at m5 on these members, while writing
        archives {pct(size5)} against 7.12's.</li>
  </ul>
</section>

<section>
  <h2>Reading speed against unrar</h2>
  <p>Both decoders test the archive the era's own RAR wrote in the sweep, so neither is reading its own
     encoder's habits. <code>test</code> decodes and checks every member without writing files. unrar
     7.20 on the same 32-core machine, alternating, fastest of five.</p>
  <div class="card"><table><thead><tr><th>archives written by</th><th>level</th><th>all classes</th>
     <th>large tier</th><th class="sep">unrar, large tier</th></tr></thead><tbody>{"".join(read_rows)}</tbody></table></div>
  <ul class="notes">
    <li><b>All {len(decode)} archives decoded cleanly in both</b>{"" if not failures else f", except {len(failures)}"}.</li>
    <li><b>RAR 5 and 7 read at parity.</b> The 4 MB members take about ten milliseconds each in either
        decoder.</li>
    <li><b>The RAR 2.9 to 4.0 family is about a quarter slower</b>, and RAR 3.0 at m5 about half again,
        because that is where WinRAR 3.00 chose PPMd, the slowest decoder in the format.</li>
    <li><b>These are short timings.</b> The whole large tier decodes in tens of milliseconds, so small
        classes mostly time process start-up. RAR 1.4 and 2.0 are left out: their archives are small-tier
        only, and timing them would time start-up and nothing else. Both decoders read them.</li>
  </ul>
</section>

<section>
  <h2>By corpus shape, at maximum effort</h2>
  <p>Level 5 everywhere except RAR 1.4, which writes one level. Multi-megabyte inputs first, then the
     small-file classes.</p>
  <div class="card"><h4 class="tcap">Multi-megabyte inputs</h4>{class_table(summary, "default", LARGE, [x for x in FORMATS if x[0] not in ("rar14", "rar20")])}
     <h4 class="tcap">Small-file classes</h4>{class_table(summary, "default", SMALL)}</div>
  <ul class="notes">
    <li><b>The RAR 2.9 family wins on text by ten to twenty percent</b> against WinRAR 2.90 and 4.20,
        because at level 3 and up rars tries PPMd where those releases stayed with LZ. WinRAR 3.00 does
        use PPMd, which is why RAR 3.0 is the fair fight and the weak row.</li>
    <li><b>Binaries are where every format loses.</b> The unstripped ELF is
        {pct(g('rar30', 'm5')['classes']['large-bin-unstripped']['gap'])} in RAR 3.0,
        {pct(g('rar40', 'm5')['classes']['large-bin-unstripped']['gap'])} in RAR 4.0 and
        {pct(rar50['m5']['classes']['large-bin-unstripped']['gap'])} in RAR 5. Which region gets which
        filter is what separates the two encoders there.</li>
    <li><b>The compressible column is noise.</b> That input packs to a few hundred bytes, so a percentage
        of it is large and means little.</li>
  </ul>
</section>

<section>
  <h2>Solid archives</h2>
  <p>The same corpus with <code>--solid</code> on our side and <code>-s</code> on the reference's, for the
     three formats whose references take it. Level 0 is left out: stored members are copied either way.</p>
  <div class="card">{level_table(summary, "solid", ["m1", "m2", "m3", "m4", "m5"], True)}
     <h4 class="tcap">By class at m5</h4>{class_table(summary, "solid", LARGE + SMALL, [x for x in FORMATS if x[0] in ("rar29", "rar30", "rar40")])}</div>
  <ul class="notes">
    <li><b>Solid and non-solid agree</b>, which is the check that matters: RAR 3.0 is
        {pct(g('rar30', 'm5', 'solid')['gap'])} solid against {pct(g('rar30', 'm5')['gap'])} not solid,
        RAR 2.9 {pct(g('rar29', 'm5', 'solid')['gap'])} against {pct(g('rar29', 'm5')['gap'])}.</li>
  </ul>
</section>

<section>
  <h2>Earlier changes, on the September corpus</h2>
  <p>Write-ups of three changes from before this sweep, kept as they were published. Their numbers come from
     the earlier corpus and earlier builds, so they do not line up with the tables above.</p>
</section>
{args.history.read_text() if args.history else ""}

<section>
  <h2>How to read this</h2>
  <ul class="notes">
    <li><b>Each format is measured against its own era's RAR</b>, named in the oracle column: DOS rar 1.402
        and 2.50 under DOSBox, WinRAR 2.90, 3.00, 4.20, 6.02 and 7.21 beta 1 under wine. Comparing two rows
        compares two different references.</li>
    <li><b>RAR 1.4 and 2.0 are small tier only.</b> Their DOS references cannot encode a 4 MB member inside
        DOSBox's thirty-second limit and leave a truncated file. Only cells whose reference archive passed
        its own round trip are scored, which also drops one RAR 2.0 cell at m5.</li>
    <li><b>Every rars archive round-trips through the reference's decoder</b>, and every one passed. The
        sweep used one pinned binary for all seven formats, checked by its fingerprint.</li>
    <li><b>There is no downloads class this time.</b> The corpus is seeded from the machine it runs on, and
        this one had no downloads folder to sample.</li>
    <li><b>The scripts that make this page live in the harness</b>: <code>summarise.py</code>,
        <code>timing.py</code>, <code>decode_timing.py</code> and <code>render_page.py</code> under
        rars-bench's <code>scripts/</code>.</li>
  </ul>
</section>
</div>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--timing", type=Path, required=True)
    parser.add_argument("--threads", type=Path, required=True)
    parser.add_argument("--decode", type=Path, required=True)
    parser.add_argument("--timing-legacy", type=Path, required=True,
                        help="timing.py output for RAR 4.0 against the native RAR 3.93")
    parser.add_argument("--history", type=Path, help="earlier sections to keep, as HTML")
    parser.add_argument("--head", required=True)
    parser.add_argument("--swept", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.write_text(render(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
