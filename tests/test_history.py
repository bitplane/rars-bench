"""The history store's layout, the machine profile's parsers and the release-result conversion."""
import json
from pathlib import Path

import pytest

from rars_bench.history import machine, store
from test_release import _result

CPUINFO = "processor\t: 0\nmodel name\t: AMD Ryzen 9 9950X3D 16-Core Processor\n"


def fake_sysfs(root: Path, domains: dict[str, str], smt: int = 16) -> None:
    """Two L3 domains on a 16-core, 32-thread part: CPU n and n+16 share a core."""
    for shared, size in domains.items():
        for cpu in machine.parse_cpu_list(shared):
            d = root / f"sys/devices/system/cpu/cpu{cpu}"
            (d / "cache/index3").mkdir(parents=True)
            (d / "cache/index3/shared_cpu_list").write_text(shared + "\n")
            (d / "cache/index3/size").write_text(size + "\n")
            (d / "topology").mkdir()
            core = cpu % smt
            (d / "topology/thread_siblings_list").write_text(f"{core},{core + smt}\n")


def test_cpu_lists_round_trip() -> None:
    assert machine.parse_cpu_list("0-3,8,10-11\n") == [0, 1, 2, 3, 8, 10, 11]
    assert machine.format_cpu_list([11, 0, 1, 2, 3, 8, 10]) == "0-3,8,10-11"
    assert machine.format_cpu_list([]) == ""


def test_cpu_slug_and_machine_id(tmp_path: Path) -> None:
    assert machine.cpu_slug("AMD Ryzen 9 9950X3D 16-Core Processor") == "amd-ryzen-9-9950x3d"
    assert machine.cpu_slug("Intel(R) Core(TM) i7-8650U CPU @ 1.90GHz") == "intel-core-i7-8650u"
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/machine-id").write_text("0123456789abcdef\n")
    mid = machine.machine_id("AMD Ryzen 9 9950X3D 16-Core Processor", tmp_path)
    assert mid.startswith("amd-ryzen-9-9950x3d-") and len(mid.rsplit("-", 1)[1]) == 6


def test_l3_domains_biggest_cache_first(tmp_path: Path) -> None:
    fake_sysfs(tmp_path, {"8-15,24-31": "32768K", "0-7,16-23": "98304K"})
    domains = machine.l3_domains(tmp_path)
    assert [d["cpus"] for d in domains] == ["0-7,16-23", "8-15,24-31"]
    assert domains[0]["size_kib"] == 98304


def test_cpu_sets_never_cpu0_and_one_domain(tmp_path: Path) -> None:
    fake_sysfs(tmp_path, {"0-7,16-23": "98304K", "8-15,24-31": "32768K"})
    domains = machine.l3_domains(tmp_path)
    allowed = list(range(32))
    cores = {c: machine.core_of(c, tmp_path) for c in allowed}
    sets = machine.cpu_sets(allowed, domains, machine.thread_counts(allowed), cores)
    assert sets["t1"] == [1]
    # Four physical cores in the big-cache domain, no SMT siblings, no CPU 0.
    assert sets["t4"] == [1, 2, 3, 4]
    assert sets["tmax"] == list(range(1, 32))
    # Fifteen threads fill the first domain's other cores, then their siblings.
    fifteen = machine.cpu_sets(allowed, domains, {"t15": 15}, cores)["t15"]
    assert 0 not in fifteen and set(fifteen) <= set(machine.parse_cpu_list("0-7,16-23"))
    assert set(range(1, 8)) <= set(fifteen)


def test_cpu_sets_respects_affinity(tmp_path: Path) -> None:
    fake_sysfs(tmp_path, {"0-7,16-23": "98304K", "8-15,24-31": "32768K"})
    domains = machine.l3_domains(tmp_path)
    allowed = [0, 8, 9, 10, 11, 12]
    sets = machine.cpu_sets(allowed, domains, machine.thread_counts(allowed))
    assert sets == {"t1": [8], "t4": [8, 9, 10, 11], "tmax": [8, 9, 10, 11, 12]}


def test_thread_labels() -> None:
    assert machine.thread_label(1, 22) == "t1"
    assert machine.thread_label(22, 22) == "tmax"
    assert machine.thread_label(16, 22) == "t16"


def test_mount_fs_longest_prefix() -> None:
    info = ("22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw\n"
            "30 22 0:40 / /tmp rw,nosuid shared:2 - tmpfs tmpfs rw\n"
            "31 22 8:17 / /data rw shared:3 - xfs /dev/sdb1 rw\n")
    assert machine.mount_fs(Path("/data/bench/scratch"), info) == "xfs"
    assert machine.mount_fs(Path("/tmp"), info) == "tmpfs"
    assert machine.mount_fs(Path("/database"), info) == "ext4"


def test_busy_others_and_warning() -> None:
    before = {10: ("encoder", 1000), 11: ("idle", 50), 12: ("gone", 1)}
    after = {10: ("encoder", 1200), 11: ("idle", 51), 13: ("new", 500)}
    busy = machine.busy_others(before, after, seconds=1.0, hz=100)
    assert busy == [{"comm": "encoder", "cpu_pct": 200}]
    assert machine.load_warning({"loadavg": [1.0, 1, 1], "others_cpu_pct": 0}, 32) is None
    assert "load average 12.0" in machine.load_warning({"loadavg": [12.0, 1, 1], "others_cpu_pct": 0}, 32)
    assert machine.load_warning({"loadavg": [1.0, 1, 1], "others_cpu_pct": 900}, 32)


def test_profile_reads_this_machine(tmp_path: Path) -> None:
    prof = machine.profile(tmp_path)
    assert prof["logical_cpus"] >= 1 and prof["id"]
    assert set(prof["cpu_sets"]) == {"t1", "t4", "tmax"}
    snap = machine.load_snapshot(sample_s=0.05)
    assert len(snap["loadavg"]) == 3


def test_record_path_layout(tmp_path: Path) -> None:
    assert store.record_path(tmp_path, "v0.13.1", "amd-x-abc123", "quick") == \
        tmp_path / "v0.13.1" / "amd-x-abc123-quick.json"
    with pytest.raises(ValueError):
        store.record_path(tmp_path, "../escape", "m", "quick")
    with pytest.raises(ValueError):
        store.record_path(tmp_path, "v1", "m", "slow")


def _sweep_scenario() -> dict:
    cell = {"format": "rar50", "solid": False, "level": 3, "class": "large-text", "input_bytes": 4000,
            "rars_status": "ok", "unrar_exit": 0, "ref_status": "ok", "ref_version": "6.02",
            "rars_packed": 1100, "ref_packed": 1000, "rars_archive": 1200, "ref_archive": 1080}
    return {"name": "formats", "doc": "d", "sweep": {"cells": [cell], "rollup": [], "failures": [], "phases": []}}


def test_from_release_cells_and_ratios() -> None:
    result = _result("full", 2.0, 1100)
    result["scenarios"].append(_sweep_scenario())
    prof = {"id": "m-abc123", "cpu_model": "x", "logical_cpus": 4}
    rec = store.from_release(result, label="44e4bb64", machine=prof, load=None, note="pre-release build")
    assert rec["tier"] == "timed" and rec["rars"]["note"] == "pre-release build"
    # threads_available is 4 in the fixture, so the t4 group is tmax.
    cell = rec["cells"]["create-rar5/text/m3/tmax/rars"]
    assert cell["ref"] == "rar" and cell["ratios"]["time"] == 2.0 and cell["ratios"]["size"] == 1.1
    assert cell["metrics"]["cpu_per_wall"] == 1.0
    size = rec["cells"]["size/rar50/plain/m3/large-text"]
    assert size["ref"] == "ref-rar50" and size["ratios"]["size"] == 1.1 and size["ok"]
    assert rec["cells"]["discmaster/rar5/rars"]["ratios"]["time"] == 0.5


def test_write_and_load_skip_old_files(tmp_path: Path) -> None:
    result = _result("full", 2.0, 1100)
    rec = store.from_release(result, label="v0.13.1", machine={"id": "m-abc123"}, load=None)
    path = store.write(tmp_path, rec)
    assert path == tmp_path / "v0.13.1" / "m-abc123-timed.json"
    (tmp_path / "baseline-x").mkdir()
    (tmp_path / "baseline-x" / "release-2026-11-23-x-full.json").write_text(json.dumps(result))
    (tmp_path / "old.json").write_text("{}")
    loaded = store.load_all(tmp_path)
    assert [r["rars"]["label"] for r in loaded] == ["v0.13.1"]
