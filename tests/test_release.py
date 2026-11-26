"""The release bench's measurement, checks and markdown, without any archiver."""
import sys
from pathlib import Path

from rars_bench.drivers.base import run_timed
from rars_bench.release import report
from rars_bench.release.discmaster import summarise
from rars_bench.release.inputs import compare_tree, manifest
from rars_bench.release.measure import measure

BIG = [sys.executable, "-c", "x = bytearray(200 << 20); x[::4096] = b'1' * len(x[::4096])"]
SMALL = [sys.executable, "-c", "pass"]


def test_peak_rss_is_each_childs_own(tmp_path: Path) -> None:
    big = measure(BIG, log=tmp_path / "big.log")
    small = measure(SMALL, log=tmp_path / "small.log")
    assert big.exit == 0 and small.exit == 0
    assert big.rss_kib > 150 * 1024
    # A RUSAGE_CHILDREN delta would report the big child's peak, or nothing, here.
    assert 0 < small.rss_kib < 100 * 1024


def test_small_child_of_a_big_parent(tmp_path: Path) -> None:
    # The high-water mark survives fork and exec, so a child started straight
    # from a 300 MiB parent would report at least 300 MiB.
    ballast = bytearray(300 << 20)
    ballast[::4096] = b"1" * len(ballast[::4096])
    small = measure(SMALL, log=tmp_path / "small.log")
    assert 0 < small.rss_kib < 100 * 1024
    del ballast


def test_run_timed_reports_small_child_after_big() -> None:
    run_timed(BIG)
    _, _, rss = run_timed(SMALL)
    assert rss is not None and rss < 100 * 1024


def test_timeout_kills(tmp_path: Path) -> None:
    m = measure([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert m.timed_out and m.exit != 0 and m.wall_s < 10


def test_compare_tree(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "a").mkdir(parents=True)
    (src / "a" / "one").write_bytes(b"1")
    (src / "two").write_bytes(b"22")
    expected = manifest(src)
    ok, _ = compare_tree(src, expected)
    assert ok
    (src / "two").write_bytes(b"23")
    ok, detail = compare_tree(src, expected)
    assert not ok and "differ" in detail


def _result(scale: str, rars_s: float, size: int) -> dict:
    def op(tool, wall, ref, out):
        return {"tool": tool, "kind": "create", "threads": 4, "reference": ref, "cmd": tool, "rounds": 2,
                "wall_s": [wall, wall], "wall_min_s": wall, "wall_median_s": wall, "cpu_s_at_min": wall,
                "rss_max_kib": 2048, "out_bytes": out, "ok": True, "detail": "unrar t ok"}
    archives = [{"path": "p/a.rar", "size": 10, "family": "rar5",
                 "tools": {"rars": {"wall_s": 0.01, "rss_kib": 1, "exit": 0},
                           "unrar": {"wall_s": 0.02, "rss_kib": 1, "exit": 0}}}]
    return {
        "meta": {"date": "2026-11-23T00:00:00+00:00", "scale": scale, "rounds": 2, "threads_available": 4,
                 "elapsed_s": 60, "tools": {"rars": {"version": "rars 0.13.0", "commit": "abc", "sha256": "f" * 64},
                                            "rar": {"version": "RAR 7.12", "sha256": "e" * 64}}},
        "scenarios": [
            {"name": "create-rar5", "doc": "doc", "groups": [
                {"name": "text m3 t4", "attrs": {"class": "text", "level": 3, "threads": 4},
                 "results": [op("rars", rars_s, False, size), op("rar", 1.0, True, 1000)]}]},
            {"name": "discmaster", "doc": "doc", "discmaster": summarise(archives, ["rars", "unrar"])},
        ],
        "cells_tsv": "x.tsv",
    }


def test_render_and_compare() -> None:
    old, new = _result("tiny", 2.0, 1100), _result("tiny", 1.0, 1050)
    text = report.render(new)
    assert "| create-rar5 | text m3 t4 | rars | rar |" in text
    assert "1.00x" in text and "+5.00%" in text
    diff = report.compare(old, new)
    assert "**-50.0%**" in diff and "**1050**" in diff


def _tools(tmp_path: Path, **peers) -> "Tools":
    from rars_bench.release.scenarios import Tools
    return Tools(rars=tmp_path / "rars", rar=tmp_path / "rar", rar_sha="", rar393=tmp_path / "rar393",
                 unrar=tmp_path / "unrar", rarfast=None, many=16, **peers)


def test_rar_rs_writes_rar5_and_rar4_beside_the_references(tmp_path: Path) -> None:
    from rars_bench.release.scenarios import creator_ops
    tools = _tools(tmp_path, rar_rs=tmp_path / "rar-rs")
    rar5 = {op.tool: op for op in creator_ops(tools, tmp_path, tmp_path, ["a.tar"], level=3, threads=4)}
    rar4 = {op.tool: op for op in creator_ops(tools, tmp_path, tmp_path, ["a.tar"], level=3, threads=4,
                                              fmt="rar40", legacy=True)}
    assert {"-ma5", "-m3", "-mt4"} <= set(rar5["rar-rs"].cmd) and not rar5["rar-rs"].reference
    assert {"-ma4", "-m3", "-mt4"} <= set(rar4["rar-rs"].cmd) and not rar4["rar-rs"].reference
    assert rar5["rar"].reference and rar4["rar393"].reference


def test_rar_rs_reads_with_its_own_unrar(tmp_path: Path) -> None:
    from rars_bench.release.scenarios import reader_ops
    tools = _tools(tmp_path, unrar_rs=tmp_path / "unrar-rs")
    ops = {op.tool: op for op in reader_ops(tools, "test", tmp_path / "a.rar", tmp_path, 4)}
    assert ops["rar-rs"].cmd[0] == str(tmp_path / "unrar-rs") and not ops["rar-rs"].reference
    assert "rar-rs" in tools.readers() and "rarfast" not in tools.readers()


def test_no_rar_rs_means_no_rar_rs_ops(tmp_path: Path) -> None:
    from rars_bench.release.scenarios import creator_ops, reader_ops
    tools = _tools(tmp_path)
    ops = creator_ops(tools, tmp_path, tmp_path, ["a.tar"], level=3, threads=4)
    ops += reader_ops(tools, "test", tmp_path / "a.rar", tmp_path, 4)
    assert all(op.tool != "rar-rs" for op in ops)
