"""Generate /home/gaz/src/tmp/rars/review.md from results/*.db.

Reads one DB per format (rar14/rar20/.../rar70.db) and emits a compression-
characteristics comparison: rars vs. each format's RAR/WinRAR oracle, sliced
by method, with per-corpus links into the corpus tree.

Run after a full sweep. Idempotent — overwrites review.md.
"""
import sqlite3
from pathlib import Path

BENCH = Path("/home/gaz/src/tmp/rars-bench")
CORPORA_REL = "../rars-bench/corpora"
OUT = Path("/home/gaz/src/tmp/rars/review.md")

FORMATS = ["rar14", "rar20", "rar29", "rar30", "rar40", "rar50", "rar70"]


def fmt_pct(rars: int | None, oracle: int | None) -> str:
    if not rars or not oracle:
        return "—"
    delta = (rars - oracle) / oracle * 100
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.1f}%"


def fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    return f"{n:,}"


def per_format_section(db_path: Path, fmt: str) -> str:
    if not db_path.is_file():
        return f"## {fmt}\n\n_No results — sweep not run or no oracle available._\n"
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # Oracle identity
    oracle_versions = [
        r["encoder_version"] for r in con.execute(
            "SELECT DISTINCT encoder_version FROM runs WHERE encoder='rar' ORDER BY 1"
        )
    ]
    rars_versions = [
        r["encoder_version"] for r in con.execute(
            "SELECT DISTINCT encoder_version FROM runs WHERE encoder='rars' ORDER BY 1"
        )
    ]
    oracle_str = ", ".join(oracle_versions) if oracle_versions else "(none)"
    rars_str = ", ".join(rars_versions) if rars_versions else "(none)"

    # Roundtrip summary
    rt = list(con.execute("""
        SELECT encoder, roundtrip_status, COUNT(*) AS n
        FROM runs GROUP BY encoder, roundtrip_status ORDER BY encoder, roundtrip_status
    """))

    out = [f"## {fmt}", "",
           f"- Oracle: **{oracle_str}**",
           f"- rars: **{rars_str}**",
           "",
           "### Roundtrip integrity", "",
           "| encoder | status | runs |",
           "|---|---|---|"]
    for r in rt:
        out.append(f"| {r['encoder']} | {r['roundtrip_status']} | {r['n']} |")
    out.append("")

    # Aggregate per method — restrict to (method, corpus_path) cells where
    # BOTH encoders produced ok rows, so the totals are apples-to-apples.
    out += [
        "### Total compressed bytes by method", "",
        "_Sums restricted to corpus files where both rars and the oracle "
        "roundtripped successfully. Cell count varies if a side failed._", "",
        "| method | files | rars | oracle | rars vs oracle |",
        "|---|---:|---:|---:|---:|",
    ]
    methods = [r[0] for r in con.execute(
        "SELECT DISTINCT method FROM runs WHERE roundtrip_status='ok' ORDER BY method"
    )]
    paired_sql = """
        WITH paired AS (
          SELECT method, corpus_path,
                 SUM(CASE WHEN encoder='rars' AND roundtrip_status='ok' THEN output_bytes END) AS rars,
                 SUM(CASE WHEN encoder='rar'  AND roundtrip_status='ok' THEN output_bytes END) AS rar
          FROM runs WHERE method = ?
          GROUP BY method, corpus_path
          HAVING rars IS NOT NULL AND rar IS NOT NULL
        )
        SELECT COUNT(*), SUM(rars), SUM(rar) FROM paired
    """
    for m in methods:
        n, rars_total, rar_total = con.execute(paired_sql, (m,)).fetchone()
        label = "0 (stored)" if m == 0 else f"{m}"
        out.append(f"| {label} | {n} | {fmt_bytes(rars_total)} | {fmt_bytes(rar_total)} | {fmt_pct(rars_total, rar_total)} |")
    out.append("")

    # Per-corpus at method 3 (representative)
    rep_method = 3 if 3 in methods else (methods[-1] if methods else None)
    if rep_method is not None:
        out += [
            f"### Per-corpus comparison at method {rep_method}", "",
            "| corpus | input | rars | oracle | gap | corpus dir |",
            "|---|---:|---:|---:|---:|---|",
        ]
        # Build per-corpus rows. Input bytes are taken once per corpus_path
        # (independent of method/encoder) so the input column doesn't shrink
        # when one side fails for some method.
        for r in con.execute("""
            WITH inputs AS (
              SELECT corpus_class, corpus_path, MAX(input_bytes) AS sz
              FROM runs GROUP BY corpus_class, corpus_path
            )
            SELECT i.corpus_class,
                   (SELECT SUM(sz) FROM inputs i2 WHERE i2.corpus_class = i.corpus_class) AS input_sz,
                   (SELECT SUM(output_bytes) FROM runs r WHERE r.method=? AND r.encoder='rars'
                       AND r.roundtrip_status='ok' AND r.corpus_class = i.corpus_class) AS rars_total,
                   (SELECT SUM(output_bytes) FROM runs r WHERE r.method=? AND r.encoder='rar'
                       AND r.roundtrip_status='ok' AND r.corpus_class = i.corpus_class) AS rar_total
            FROM inputs i
            GROUP BY i.corpus_class
            ORDER BY i.corpus_class
        """, (rep_method, rep_method)):
            link = f"[`{r['corpus_class']}/`]({CORPORA_REL}/{r['corpus_class']}/)"
            out.append(
                f"| {r['corpus_class']} | {fmt_bytes(r['input_sz'])} | "
                f"{fmt_bytes(r['rars_total'])} | {fmt_bytes(r['rar_total'])} | "
                f"{fmt_pct(r['rars_total'], r['rar_total'])} | {link} |"
            )
        out.append("")

    # Encode timing per method, for both encoders
    out += ["### Encode time by method (seconds, full corpus)", "",
            "_Oracle times for rar14/rar20 include DOSBox-X emulation overhead "
            "(~1-30s per call). rar29-rar70 oracles run under Wine (~2s startup "
            "per call). rars is invoked natively._", "",
            "| method | rars (s) | oracle (s) | ratio |",
            "|---|---:|---:|---:|"]
    for m in methods:
        r_t, o_t = con.execute("""
            SELECT
              SUM(CASE WHEN encoder='rars' AND roundtrip_status='ok' THEN encode_ms END)/1000.0,
              SUM(CASE WHEN encoder='rar'  AND roundtrip_status='ok' THEN encode_ms END)/1000.0
            FROM runs WHERE method=?
        """, (m,)).fetchone()
        label = "0 (stored)" if m == 0 else f"{m}"
        ratio = "—"
        if r_t and o_t:
            ratio = f"{r_t/o_t:.2f}×"
        rt_s = f"{r_t:.2f}" if r_t is not None else "—"
        ot_s = f"{o_t:.2f}" if o_t is not None else "—"
        out.append(f"| {label} | {rt_s} | {ot_s} | {ratio} |")
    out.append("")

    # Method response curve: do rars's levels actually differentiate?
    if len([m for m in methods if m and m > 0]) >= 2:
        out += ["### Level differentiation (does rars `--level` actually change output?)", ""]
        non_stored = [m for m in methods if m and m > 0]
        sizes = {}
        for m in non_stored:
            v = con.execute(
                "SELECT SUM(output_bytes) FROM runs WHERE encoder='rars' AND method=? AND roundtrip_status='ok'",
                (m,)
            ).fetchone()[0]
            sizes[m] = v
        unique = len({v for v in sizes.values() if v is not None})
        verdict = "✅ levels differ" if unique > 1 else "❌ **all levels produce identical bytes**"
        out.append(f"{verdict}")
        out.append("")
        out.append("| level | rars total |")
        out.append("|---|---:|")
        for m, v in sizes.items():
            out.append(f"| {m} | {fmt_bytes(v)} |")
        out.append("")

    # Failed runs
    failed = list(con.execute("""
        SELECT encoder, encoder_version, corpus_class, corpus_path, method, roundtrip_status
        FROM runs WHERE roundtrip_status != 'ok'
        ORDER BY encoder, method, corpus_path
    """))
    if failed:
        out += [f"### Failed runs ({len(failed)})", "",
                "| encoder | version | method | corpus | status | path |",
                "|---|---|---|---|---|---|"]
        for r in failed:
            link = f"[`{r['corpus_path']}`]({CORPORA_REL}/{r['corpus_path']})"
            out.append(
                f"| {r['encoder']} | {r['encoder_version']} | {r['method']} | "
                f"{r['corpus_class']} | {r['roundtrip_status']} | {link} |"
            )
        out.append("")

    return "\n".join(out)


def main():
    sections = ["# rars compression benchmark — review", "",
                "_Generated by `rars-bench` sweep across all supported formats. "
                "Each format is benchmarked against the appropriate oracle RAR/WinRAR "
                "binary running natively (DOSBox-X for DOS-era, Wine for Win32)._", "",
                "## Summary", ""]

    # Top-level summary table — one row per format
    summary_rows = []
    for fmt in FORMATS:
        db = BENCH / "results" / f"{fmt}.db"
        if not db.is_file():
            summary_rows.append((fmt, "—", "—", "—", "—", "no DB"))
            continue
        con = sqlite3.connect(str(db))
        oracle = con.execute(
            "SELECT DISTINCT encoder_version FROM runs WHERE encoder='rar' LIMIT 1"
        ).fetchone()
        oracle_v = oracle[0] if oracle else "(none)"
        # Median-method gap at method 3 (or first available)
        m_row = con.execute(
            "SELECT DISTINCT method FROM runs WHERE method > 0 AND roundtrip_status='ok' ORDER BY method"
        ).fetchall()
        rep = 3 if any(r[0] == 3 for r in m_row) else (m_row[0][0] if m_row else None)
        if rep is not None:
            # Pairwise (only cells where both sides succeeded)
            n, r_rars, r_rar = con.execute("""
                WITH paired AS (
                  SELECT corpus_path,
                         SUM(CASE WHEN encoder='rars' AND roundtrip_status='ok' THEN output_bytes END) AS rars,
                         SUM(CASE WHEN encoder='rar'  AND roundtrip_status='ok' THEN output_bytes END) AS rar
                  FROM runs WHERE method = ?
                  GROUP BY corpus_path
                  HAVING rars IS NOT NULL AND rar IS NOT NULL
                )
                SELECT COUNT(*), SUM(rars), SUM(rar) FROM paired
            """, (rep,)).fetchone()
            gap = fmt_pct(r_rars, r_rar)
        else:
            rep, r_rars, r_rar, gap = "—", None, None, "—"
        # Levels differ?
        sizes = set()
        for r in con.execute(
            "SELECT method, SUM(output_bytes) FROM runs "
            "WHERE encoder='rars' AND method > 0 AND roundtrip_status='ok' GROUP BY method"
        ):
            if r[1]: sizes.add(r[1])
        lvl_verdict = "✅" if len(sizes) > 1 else ("❌" if len(sizes) == 1 else "—")
        # Roundtrip pass rate
        ok = con.execute("SELECT COUNT(*) FROM runs WHERE roundtrip_status='ok'").fetchone()[0]
        tot = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        summary_rows.append((fmt, oracle_v, f"{ok}/{tot}", f"m{rep}: {gap}", lvl_verdict,
                             f"[details](#{fmt})"))

    sections.append("| format | oracle | roundtrip ok | rars vs oracle | levels differ | |")
    sections.append("|---|---|---|---|---|---|")
    for row in summary_rows:
        sections.append("| " + " | ".join(str(x) for x in row) + " |")
    sections.append("")

    # Cross-format compression curve: rars-total bytes per level, per format,
    # over corpus files where rars roundtripped at every level (the
    # apples-to-apples set for level differentiation).
    sections += [
        "## Level differentiation across all formats", "",
        "Total bytes emitted by rars per level, across the full corpus "
        "(only files that roundtripped at every level). Flat rows mean "
        "rars's `--level` is currently a no-op for that format.", "",
        "| format | m1 | m2 | m3 | m4 | m5 | varies? |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for fmt in FORMATS:
        db = BENCH / "results" / f"{fmt}.db"
        if not db.is_file():
            sections.append(f"| {fmt} | — | — | — | — | — | — |")
            continue
        con = sqlite3.connect(str(db))
        levels = [1, 2, 3, 4, 5]
        sizes = []
        for m in levels:
            v = con.execute(
                "SELECT SUM(output_bytes) FROM runs "
                "WHERE encoder='rars' AND method=? AND roundtrip_status='ok'",
                (m,)
            ).fetchone()[0]
            sizes.append(v)
        unique = {v for v in sizes if v is not None}
        varies = "—" if not unique else ("✅" if len(unique) > 1 else "❌")
        row_cells = " | ".join(fmt_bytes(v) for v in sizes)
        sections.append(f"| {fmt} | {row_cells} | {varies} |")
    sections.append("")

    for fmt in FORMATS:
        db = BENCH / "results" / f"{fmt}.db"
        sections.append(per_format_section(db, fmt))

    sections.append("---")
    sections.append("")
    sections.append("_Corpus: 10 classes, 149 files, ~1.8 MB total. "
                    "See [`rars-bench/corpora/`](../rars-bench/corpora/) for source._")

    OUT.write_text("\n".join(sections))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
