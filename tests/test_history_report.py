"""The history report: column order, what turns bold, ratio-only columns from other machines."""
from rars_bench.history import report


def record(label, date, cells, machine="m-aaaaaa", tools=None, tier="quick", load=None):
    return {"schema": 1, "tier": tier, "date": date, "rars": {"label": label, "version": f"rars {label}",
            "commit": label * 3, "commit_date": date}, "machine": {"id": machine, "logical_cpus": 32},
            "tools": tools or {"ref-rar50": {"version": "WinRAR 6.02", "sha256": "a" * 64},
                               "rar": {"version": "RAR 7.12", "sha256": "b" * 64}},
            "cells": cells, "load": load, "checks": {}}


def size(packed, ref=1000, ok=True):
    return {"ref": "ref-rar50", "ok": ok, "metrics": {"packed": packed, "status": "ok"},
            "ratios": {"size": packed / ref}}


def timed(t, m, ok=True):
    return {"ref": "rar", "ok": ok, "metrics": {"wall_s": t}, "ratios": {"time": t, "rss": m}}


S = "size/rar50/plain/m3/text"
T = "create-rar5/text/m3/t1/rars"


def test_sizes_bold_on_any_change_and_order_by_commit() -> None:
    recs = [record("v2", "2026-11-24", {S: size(1001)}), record("v1", "2026-11-17", {S: size(1000)}),
            record("v3", "2026-11-25", {S: size(1001)})]
    text = report.render(recs, machine="m-aaaaaa", tier="quick")
    row = next(line for line in text.splitlines() if line.startswith("| plain/m3/text |"))
    assert row == "| plain/m3/text | 1,000 (+0.00%) | **1,001** (**+0.10%**) | 1,001 (+0.10%) |"
    assert "| cell | v1 | v2 | v3 |" in text


def test_timings_bold_past_threshold_and_failures() -> None:
    recs = [record("v1", "2026-11-17", {T: timed(2.0, 1.0)}, tier="timed"),
            record("v2", "2026-11-24", {T: timed(2.06, 1.2)}, tier="timed"),
            record("v3", "2026-11-25", {T: timed(1.5, 1.2, ok=False)}, tier="timed")]
    text = report.render(recs, machine="m-aaaaaa", tier="timed")
    row = next(line for line in text.splitlines() if line.startswith("| text/m3/t1/rars |"))
    cols = [c.strip() for c in row.split("|")[2:-1]]
    assert cols == ["t 2.00x m 1.00x", "t 2.06x m **1.20x**", "t **1.50x** m 1.20x **FAIL**"]


def test_changed_reference_gets_a_dagger_and_a_note() -> None:
    newer = {"ref-rar50": {"version": "WinRAR 6.24", "sha256": "c" * 64}, "rar": {"version": "RAR 7.12"}}
    recs = [record("v1", "2026-11-17", {S: size(1000)}), record("v2", "2026-11-24", {S: size(1000, 990)}, tools=newer)]
    text = report.render(recs, machine="m-aaaaaa", tier="quick")
    assert "tool ref-rar50: version WinRAR 6.02 -> WinRAR 6.24" in text
    assert "†" in next(line for line in text.splitlines() if line.startswith("| plain/m3/text |")).split("|")[3]


def test_other_machines_fill_gaps_ratio_only() -> None:
    recs = [record("v1", "2026-11-17", {S: size(1000)}),
            record("v2", "2026-11-24", {S: size(1001)}, machine="m-bbbbbb"),
            record("v1", "2026-11-17", {S: size(1000)}, machine="m-bbbbbb")]
    text = report.render(recs, machine="m-aaaaaa", tier="quick", others=True)
    assert "| cell | v1 | v2 (ratio-only) |" in text
    row = next(line for line in text.splitlines() if line.startswith("| plain/m3/text |"))
    assert row.split("|")[3].strip() == "**+0.10%**"
    assert "other: `m-bbbbbb`, ratio-only" in text
    assert "(ratio-only)" not in report.render(recs, machine="m-aaaaaa", tier="quick")


def test_changed_only_and_load_note() -> None:
    busy = {"start": {"loadavg": [30.0, 1, 1], "others_cpu_pct": 0}}
    recs = [record("v1", "2026-11-17", {S: size(1000), "size/rar50/plain/m3/logs": size(5)}),
            record("v2", "2026-11-24", {S: size(999), "size/rar50/plain/m3/logs": size(5)}, load=busy)]
    text = report.render(recs, machine="m-aaaaaa", tier="quick", changed_only=True)
    assert "| plain/m3/text |" in text and "| plain/m3/logs |" not in text
    assert "busy at start: load average 30.0" in text


def test_default_machine_prefers_here() -> None:
    recs = [record("v1", "d", {}, machine="a"), record("v2", "d", {}, machine="b"), record("v3", "d", {}, machine="b")]
    assert report.default_machine(recs, "a") == "a"
    assert report.default_machine(recs, "zzz") == "b"
