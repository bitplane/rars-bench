"""Render one or more sweep DBs as a self-contained HTML report.

Text triage answers "which cell is worst". A chart answers "what shape is the
gap", which is the question you have when deciding where to spend effort:
whether we trail everywhere by a little or somewhere by a lot, and whether it
moves with compression level.

Several DBs render as stacked sections, which is how you ask the two questions
that matter after a change: did this help, and does the answer depend on which
corpus you asked. The second is not hypothetical here — the small tier reports
about a third of the gap the large tier does.

Everything is inline. No CDN, no matplotlib, no build step, so the output
survives being emailed, committed, or opened off a USB stick in five years.

One judgement is baked in. A gap percentage computed on a near-empty archive is
noise: a degenerate input compressing to 480 bytes against our 2316 reads as
+382%, which would top every ranking while meaning 1836 bytes. Cells below
DEGENERATE_BYTES are drawn muted and left out of the totals, with a note rather
than a silent drop.
"""
from __future__ import annotations

import datetime as _dt
import html
import sqlite3
from dataclasses import dataclass
from pathlib import Path

#: Archives smaller than this make the relative gap meaningless.
DEGENERATE_BYTES = 64 * 1024

#: Clamp for the bar scale, so one outlier does not flatten everything else.
MAX_PLOTTED_GAP = 20.0


@dataclass
class Section:
    label: str
    fmt: str
    oracle: str
    oracle_version: str
    methods: list[int]
    classes: list[str]
    cells: dict[tuple[str, int], dict[str, tuple[int, int]]]


def load(db_path: Path, label: str | None = None) -> Section:
    db = sqlite3.connect(db_path)
    cells: dict[tuple[str, int], dict[str, tuple[int, int]]] = {}
    for corpus_class, encoder, method, input_bytes, output_bytes in db.execute(
        "select corpus_class, encoder, method, input_bytes, output_bytes from runs"
    ):
        cells.setdefault((corpus_class, method), {})[encoder] = (input_bytes, output_bytes)
    versions = dict(db.execute("select distinct encoder, encoder_version from runs"))
    row = db.execute("select distinct format from runs").fetchone()
    db.close()
    if not cells:
        raise SystemExit(f"no rows in {db_path}")
    oracle = next((e for e in versions if e != "rars"), "rar")
    return Section(
        label=label or db_path.stem,
        fmt=row[0] if row else "?",
        oracle=oracle,
        oracle_version=versions.get(oracle, ""),
        methods=sorted({m for _, m in cells}),
        classes=sorted({c for c, _ in cells}),
        cells=cells,
    )


def _bar(gap: float, degenerate: bool) -> str:
    """One horizontal bar centred on zero. Left of centre beats the oracle."""
    span = min(abs(gap), MAX_PLOTTED_GAP) / MAX_PLOTTED_GAP * 50.0
    x = 50.0 if gap >= 0 else 50.0 - span
    kind = "degenerate" if degenerate else ("worse" if gap >= 0 else "better")
    clipped = " clipped" if abs(gap) > MAX_PLOTTED_GAP else ""
    return (
        '<svg class="bar" viewBox="0 0 100 12" preserveAspectRatio="none" '
        f'role="img" aria-label="{gap:+.1f} percent">'
        '<line class="axis" x1="50" y1="0" x2="50" y2="12"/>'
        f'<rect class="{kind}{clipped}" x="{x:.2f}" y="2" '
        f'width="{max(span, 0.6):.2f}" height="8" rx="0.5"/>'
        "</svg>"
    )


def _totals(section: Section, method: int) -> tuple[int, int] | None:
    counted = [
        cell
        for (_c, m), cell in section.cells.items()
        if m == method
        and len(cell) > 1
        and max(cell[section.oracle][1], cell["rars"][1]) >= DEGENERATE_BYTES
    ]
    if not counted:
        return None
    return (
        sum(cell["rars"][1] for cell in counted),
        sum(cell[section.oracle][1] for cell in counted),
    )


def _headline(sections: list[Section]) -> str:
    """The one number per section, at the level people actually use."""
    cards = []
    for section in sections:
        method = 3 if 3 in section.methods else section.methods[-1]
        totals = _totals(section, method)
        if totals is None:
            continue
        rars_bytes, oracle_bytes = totals
        gap = (rars_bytes - oracle_bytes) * 100.0 / oracle_bytes
        cards.append(
            f'<div class="card"><p class="card-label">{html.escape(section.label)}</p>'
            f'<p class="card-figure {"worse-text" if gap >= 0 else "better-text"}">{gap:+.1f}%</p>'
            f'<p class="card-sub">at m{method} &middot; {rars_bytes:,} against {oracle_bytes:,} bytes</p></div>'
        )
    return f'<section class="cards">{"".join(cards)}</section>'


def _summary_matrix(sections: list[Section]) -> str:
    """Every section's weighted total, one row each.

    With one or two sections the big figures read fine. Past that they stop
    being a headline and start being a wall, and the question changes from
    "how bad is it" to "does it differ by format", which wants a matrix.
    """
    methods = sorted({m for section in sections for m in section.methods})
    heads = "".join(f"<th>m{m}</th>" for m in methods)
    rows = []
    for section in sections:
        tds = [f'<th scope="row">{html.escape(section.label)}</th>']
        for method in methods:
            totals = _totals(section, method) if method in section.methods else None
            if totals is None:
                tds.append('<td class="missing">&mdash;</td>')
                continue
            rars_bytes, oracle_bytes = totals
            gap = (rars_bytes - oracle_bytes) * 100.0 / oracle_bytes
            tds.append(
                f"<td>{_bar(gap, False)}"
                f'<span class="gap strong">{gap:+.1f}%</span></td>'
            )
        rows.append("<tr>" + "".join(tds) + "</tr>")
    return f"""
  <section class="block">
    <h2>Every format, weighted by bytes</h2>
    <p class="block-sub">Same corpus throughout, so the rows compare directly.
      Each cell excludes the degenerate classes; per-class detail is below.</p>
    <div class="scroll">
      <table>
        <thead><tr><th>format</th>{heads}</tr></thead>
        <tbody>{"".join(rows)}</tbody>
      </table>
    </div>
  </section>"""


def _table(section: Section) -> str:
    heads = "".join(f"<th>m{m}</th>" for m in section.methods)
    rows = []
    for corpus_class in section.classes:
        tds = [f'<th scope="row">{html.escape(corpus_class)}</th>']
        for method in section.methods:
            cell = section.cells.get((corpus_class, method), {})
            if len(cell) < 2:
                tds.append('<td class="missing">&mdash;</td>')
                continue
            oracle_bytes = cell[section.oracle][1]
            rars_bytes = cell["rars"][1]
            gap = (rars_bytes - oracle_bytes) * 100.0 / oracle_bytes
            degenerate = max(oracle_bytes, rars_bytes) < DEGENERATE_BYTES
            title = " title='both archives under 64 KiB; the ratio means nothing here'" if degenerate else ""
            klass = "gap muted" if degenerate else "gap"
            tds.append(
                f"<td{title}>{_bar(gap, degenerate)}"
                f'<span class="{klass}">{gap:+.1f}%</span>'
                f'<span class="bytes">{rars_bytes:,} / {oracle_bytes:,}</span></td>'
            )
        rows.append("<tr>" + "".join(tds) + "</tr>")

    foot = []
    for method in section.methods:
        totals = _totals(section, method)
        if totals is None:
            foot.append('<td class="missing">&mdash;</td>')
            continue
        rars_bytes, oracle_bytes = totals
        gap = (rars_bytes - oracle_bytes) * 100.0 / oracle_bytes
        foot.append(
            f'<td>{_bar(gap, False)}<span class="gap strong">{gap:+.1f}%</span>'
            f'<span class="bytes">{rars_bytes:,} / {oracle_bytes:,}</span></td>'
        )

    return f"""
  <section class="block">
    <h2>{html.escape(section.label)}</h2>
    <p class="block-sub">{html.escape(section.fmt)} against
      {html.escape(section.oracle)} {html.escape(section.oracle_version)},
      {len(section.classes)} classes</p>
    <div class="scroll">
      <table>
        <thead><tr><th>corpus class</th>{heads}</tr></thead>
        <tbody>{"".join(rows)}</tbody>
        <tfoot><tr><th scope="row">weighted by bytes</th>{"".join(foot)}</tr></tfoot>
      </table>
    </div>
  </section>"""


def render(db_paths: list[tuple[Path, str | None]], title: str | None = None) -> str:
    sections = [load(path, label) for path, label in db_paths]
    stamp = _dt.datetime.now().astimezone().strftime("%d %B %Y, %H:%M %Z")
    title = title or "RAR 5 Compression Gap"

    return f"""<title>{html.escape(title)}</title>
<style>
  :root {{
    --ground: #f7f6f3;
    --surface: #fffefc;
    --ink: #1c1b21;
    --muted: #6d6b76;
    --rule: #e2dfd8;
    --rule-strong: #c9c5bb;
    --worse: #ad3a2c;
    --better: #3d7550;
    --inert: #c3bfb6;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --ground: #131318;
      --surface: #1b1b22;
      --ink: #eae7f0;
      --muted: #9997a3;
      --rule: #2c2c36;
      --rule-strong: #45444f;
      --worse: #dd7a6c;
      --better: #6cba86;
      --inert: #4d4c57;
    }}
  }}
  :root[data-theme="dark"] {{
    --ground: #131318;
    --surface: #1b1b22;
    --ink: #eae7f0;
    --muted: #9997a3;
    --rule: #2c2c36;
    --rule-strong: #45444f;
    --worse: #dd7a6c;
    --better: #6cba86;
    --inert: #4d4c57;
  }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 3.5rem 1.5rem 5rem;
    background: var(--ground);
    color: var(--ink);
    font: 15px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  main {{ max-width: 68rem; margin: 0 auto; display: flex; flex-direction: column; gap: 2.75rem; }}

  header {{ display: flex; flex-direction: column; gap: .4rem; }}
  h1 {{
    margin: 0;
    font-family: Charter, "Bitstream Charter", "Iowan Old Style", "Palatino Linotype", Georgia, serif;
    font-size: clamp(1.75rem, 4vw, 2.4rem);
    font-weight: 600;
    letter-spacing: -.015em;
    text-wrap: balance;
  }}
  .lede {{ margin: 0; color: var(--muted); max-width: 46rem; }}
  .stamp {{
    margin: 0; color: var(--muted); font-size: .78rem;
    text-transform: uppercase; letter-spacing: .09em;
  }}

  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); gap: 1rem; }}
  .card {{
    background: var(--surface); border: 1px solid var(--rule);
    padding: 1.1rem 1.25rem; display: flex; flex-direction: column; gap: .15rem;
  }}
  .card-label {{
    margin: 0; font-size: .74rem; text-transform: uppercase;
    letter-spacing: .1em; color: var(--muted);
  }}
  .card-figure {{
    margin: .2rem 0 0;
    font-family: Charter, "Bitstream Charter", "Iowan Old Style", Georgia, serif;
    font-size: 2.4rem; font-weight: 600; line-height: 1;
    font-variant-numeric: tabular-nums;
  }}
  .card-sub {{ margin: 0; font-size: .8rem; color: var(--muted); font-variant-numeric: tabular-nums; }}
  .worse-text {{ color: var(--worse); }}
  .better-text {{ color: var(--better); }}

  .block {{ display: flex; flex-direction: column; gap: .3rem; }}
  h2 {{
    margin: 0;
    font-family: Charter, "Bitstream Charter", "Iowan Old Style", Georgia, serif;
    font-size: 1.2rem; font-weight: 600; letter-spacing: -.005em;
  }}
  .block-sub {{ margin: 0 0 .85rem; color: var(--muted); font-size: .84rem; }}

  .scroll {{ overflow-x: auto; border: 1px solid var(--rule); background: var(--surface); }}
  table {{ border-collapse: collapse; width: 100%; min-width: 46rem; }}
  th, td {{ text-align: left; padding: .6rem .8rem; border-bottom: 1px solid var(--rule); vertical-align: top; }}
  thead th {{
    font-size: .72rem; text-transform: uppercase; letter-spacing: .09em;
    color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--rule-strong);
  }}
  th[scope="row"] {{
    font-family: ui-monospace, "SF Mono", "Cascadia Code", Menlo, monospace;
    font-size: .8rem; font-weight: 500; white-space: nowrap;
  }}
  tbody tr:last-child td, tbody tr:last-child th {{ border-bottom: none; }}
  tfoot td, tfoot th {{ border-bottom: none; border-top: 1px solid var(--rule-strong); }}
  tfoot th[scope="row"] {{ font-weight: 600; }}

  .bar {{ display: block; width: 100%; height: 12px; }}
  .bar .axis {{ stroke: var(--rule-strong); stroke-width: 1; vector-effect: non-scaling-stroke; }}
  .bar .worse {{ fill: var(--worse); }}
  .bar .better {{ fill: var(--better); }}
  .bar .degenerate {{ fill: var(--inert); }}
  .bar .clipped {{ opacity: .5; }}

  .gap {{
    display: block; margin-top: .3rem; font-size: .82rem;
    font-variant-numeric: tabular-nums;
    font-family: ui-monospace, "SF Mono", "Cascadia Code", Menlo, monospace;
  }}
  .gap.strong {{ font-weight: 700; }}
  .gap.muted {{ color: var(--muted); }}
  .bytes {{
    display: block; font-size: .7rem; color: var(--muted);
    font-variant-numeric: tabular-nums;
    font-family: ui-monospace, "SF Mono", "Cascadia Code", Menlo, monospace;
  }}
  .missing {{ color: var(--muted); }}

  .notes {{ border-top: 1px solid var(--rule); padding-top: 1.25rem; }}
  .notes h2 {{ margin-bottom: .6rem; }}
  .notes ul {{ margin: 0; padding-left: 1.1rem; color: var(--muted); font-size: .88rem; }}
  .notes li {{ margin-bottom: .5rem; }}
  .notes strong {{ color: var(--ink); font-weight: 600; }}
</style>
<main>
  <header>
    <p class="stamp">rars-bench &middot; {stamp}</p>
    <h1>{html.escape(title)}</h1>
    <p class="lede">
      Every bar runs from the centre line.
      <strong class="worse-text">Right</strong> means our archive is bigger than
      WinRAR's; <strong class="better-text">left</strong> means smaller. The scale
      clamps at &plusmn;{MAX_PLOTTED_GAP:g}%, and clamped bars are drawn faint.
    </p>
  </header>

  {_headline(sections) if len(sections) <= 2 else _summary_matrix(sections)}
  {"".join(_table(section) for section in sections)}

  <section class="notes">
    <h2>Reading this</h2>
    <ul>
      <li><strong>Totals sum bytes, they do not average percentages.</strong> A
        4 MB class counts for more than a 40 KB one, which is the honest
        weighting when the question is how big the archive comes out.</li>
      <li><strong>Grey cells are degenerate.</strong> Where both archives land
        under {DEGENERATE_BYTES // 1024} KiB the ratio stops meaning anything:
        1.8 KB of difference on a 4 MB run of zeroes reads as +380%. Those cells
        are excluded from the totals.</li>
      <li><strong>No timings.</strong> The oracle runs under wine and part of its
        timing is replayed from a cache, so those numbers describe neither
        tool's speed. Bytes are exact; time is measured elsewhere.</li>
    </ul>
  </section>
</main>
"""


def write_report(
    db_paths: list[tuple[Path, str | None]],
    out_path: Path,
    title: str | None = None,
) -> Path:
    out_path.write_text(render(db_paths, title), encoding="utf-8")
    return out_path
