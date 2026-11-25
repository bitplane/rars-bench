"""What machine a result came from, how busy it was, and which CPUs each thread count ran on.

Everything reads from a root directory (`/` by default) so the parsers can be
tested against a fake `/proc` and `/sys`. Nothing here names a person or a
host: the machine id is a slug of the CPU model plus a short hash of
`/etc/machine-id`, which tells two boxes apart without saying which they are.
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import time
from pathlib import Path

#: The thread labels cells are stored under. `tmax` is every CPU the run may
#: use except CPU 0, so it means "all of this machine" on any machine.
THREAD_LABELS = ("t1", "t4", "tmax")

#: Load above this share of the logical CPUs gets a warning in the report.
BUSY_SHARE = 0.25


def parse_cpu_list(text: str) -> list[int]:
    """`0-7,16-23` -> [0..7, 16..23]."""
    cpus: list[int] = []
    for part in text.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            cpus.extend(range(int(lo), int(hi) + 1))
        else:
            cpus.append(int(part))
    return sorted(set(cpus))


def format_cpu_list(cpus: list[int]) -> str:
    """[1, 2, 3, 5] -> `1-3,5`."""
    out, run = [], []
    for cpu in sorted(set(cpus)):
        if run and cpu == run[-1] + 1:
            run.append(cpu)
            continue
        if run:
            out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
        run = [cpu]
    if run:
        out.append(f"{run[0]}-{run[-1]}" if len(run) > 1 else str(run[0]))
    return ",".join(out)


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def cpu_model(root: Path = Path("/")) -> str:
    text = _read(root / "proc/cpuinfo") or ""
    for line in text.splitlines():
        if line.startswith("model name") and ":" in line:
            return line.split(":", 1)[1].strip()
    return platform.processor() or platform.machine() or "unknown"


def cpu_slug(model: str) -> str:
    """`AMD Ryzen 9 9950X3D 16-Core Processor` -> `amd-ryzen-9-9950x3d`."""
    s = model.lower()
    s = re.sub(r"\((r|tm)\)", " ", s)
    s = re.sub(r"@.*$", " ", s)
    s = re.sub(r"\b\d+-core\b|\bprocessor\b|\bcpu\b|\bwith\b.*$", " ", s)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown-cpu"


def machine_id(model: str, root: Path = Path("/")) -> str:
    """CPU slug plus six hex digits of the machine id: stable, and names no one."""
    seed = (_read(root / "etc/machine-id") or "").strip() or platform.node()
    return f"{cpu_slug(model)}-{hashlib.sha256(seed.encode()).hexdigest()[:6]}"


def l3_domains(root: Path = Path("/")) -> list[dict]:
    """One entry per distinct L3: its CPUs and size, biggest cache first, then lowest CPU."""
    base = root / "sys/devices/system/cpu"
    seen: dict[str, dict] = {}
    for cpu_dir in sorted(base.glob("cpu[0-9]*"), key=lambda p: int(p.name[3:])):
        index3 = cpu_dir / "cache" / "index3"
        shared = _read(index3 / "shared_cpu_list")
        if shared is None:
            continue
        key = shared.strip()
        if key in seen:
            continue
        size = (_read(index3 / "size") or "").strip()
        kib = int(size[:-1]) * (1024 if size.endswith("M") else 1) if size[:-1].isdigit() else None
        seen[key] = {"cpus": format_cpu_list(parse_cpu_list(key)), "size_kib": kib}
    return sorted(seen.values(), key=lambda d: (-(d["size_kib"] or 0), parse_cpu_list(d["cpus"])[0]))


def core_of(cpu: int, root: Path = Path("/")) -> int:
    """The lowest CPU sharing this one's core, which names the core."""
    text = _read(root / f"sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
    return parse_cpu_list(text)[0] if text else cpu


def cpu_sets(allowed: list[int], domains: list[dict], counts: dict[str, int],
             cores: dict[int, int] | None = None) -> dict[str, list[int]]:
    """The CPUs each thread label runs on. Never CPU 0.

    A count that fits in one L3 domain gets CPUs from the first domain it fits
    in (biggest cache first), one per physical core before any SMT sibling,
    and the sibling of CPU 0 last, since CPU 0 takes the machine's interrupts. A
    count that fits in none takes every allowed CPU but CPU 0.
    """
    cores = cores or {}
    usable = [c for c in sorted(allowed) if c != 0]
    out: dict[str, list[int]] = {}
    for label, count in counts.items():
        chosen = None
        for dom in domains:
            members = [c for c in parse_cpu_list(dom["cpus"]) if c in usable]
            if len(members) < count:
                continue
            # One CPU per core first, then SMT siblings, and CPU 0's sibling last of all.
            firsts, seconds, lasts, used = [], [], [], set()
            for c in members:
                core = cores.get(c, c)
                if core == cores.get(0, 0):
                    lasts.append(c)
                else:
                    (seconds if core in used else firsts).append(c)
                used.add(core)
            chosen = sorted((firsts + seconds + lasts)[:count])
            break
        out[label] = chosen if chosen is not None else usable[:count]
    return out


def thread_counts(allowed: list[int]) -> dict[str, int]:
    usable = len([c for c in allowed if c != 0]) or 1
    return {"t1": 1, "t4": min(4, usable), "tmax": usable}


def thread_label(threads: int | None, many: int) -> str:
    """The stored label for a thread count: t1, t4, tmax, or t<n> for anything else."""
    if threads is None:
        return "tdefault"
    if threads == many:
        return "tmax"
    return f"t{threads}"


def mount_fs(path: Path, mountinfo: str) -> str:
    """Filesystem type of the longest mount point containing `path`, from /proc/self/mountinfo text."""
    best, fstype = "", "unknown"
    target = str(path)
    for line in mountinfo.splitlines():
        left, _, right = line.partition(" - ")
        fields = left.split()
        if len(fields) < 5 or not right:
            continue
        mount = fields[4].replace("\\040", " ")
        inside = target == mount or target.startswith(mount.rstrip("/") + "/") or mount == "/"
        if inside and len(mount) > len(best):
            best, fstype = mount, right.split()[0]
    return fstype


def _proc_cpu_ticks(root: Path, own_uid: int) -> dict[int, tuple[str, int]]:
    """pid -> (comm, utime + stime) for every process not run by `own_uid`."""
    out = {}
    for d in (root / "proc").iterdir() if (root / "proc").is_dir() else []:
        if not d.name.isdigit():
            continue
        stat, status = _read(d / "stat"), _read(d / "status")
        if not stat or not status:
            continue
        uid = next((int(line.split()[1]) for line in status.splitlines() if line.startswith("Uid:")), None)
        if uid is None or uid == own_uid:
            continue
        comm = stat[stat.find("(") + 1:stat.rfind(")")]
        rest = stat[stat.rfind(")") + 2:].split()
        try:
            out[int(d.name)] = (comm, int(rest[11]) + int(rest[12]))
        except (IndexError, ValueError):
            continue
    return out


def busy_others(before: dict, after: dict, seconds: float, hz: int = 100, floor_pct: float = 10.0) -> list[dict]:
    """Other users' processes over `floor_pct` CPU between two samples, busiest first. No names of people."""
    busy = []
    for pid, (comm, ticks) in after.items():
        if pid not in before:
            continue
        pct = (ticks - before[pid][1]) / hz / seconds * 100
        if pct >= floor_pct:
            busy.append({"comm": comm, "cpu_pct": round(pct)})
    return sorted(busy, key=lambda b: -b["cpu_pct"])


def load_snapshot(root: Path = Path("/"), sample_s: float = 1.0) -> dict:
    """Load average and other users' busy processes, sampled over `sample_s`."""
    loadavg = (_read(root / "proc/loadavg") or "0 0 0").split()[:3]
    own = os.getuid()
    before = _proc_cpu_ticks(root, own)
    time.sleep(sample_s)
    after = _proc_cpu_ticks(root, own)
    hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    busy = busy_others(before, after, sample_s, hz)
    return {"loadavg": [float(x) for x in loadavg], "others_busy": busy[:10],
            "others_cpu_pct": sum(b["cpu_pct"] for b in busy)}


def load_warning(snapshot: dict | None, logical_cpus: int) -> str | None:
    """A sentence for the report when the machine was busy, or None."""
    if not snapshot:
        return None
    one = snapshot["loadavg"][0]
    others = snapshot.get("others_cpu_pct", 0)
    if one > BUSY_SHARE * logical_cpus or others > BUSY_SHARE * logical_cpus * 100:
        return (f"load average {one:.1f} on {logical_cpus} CPUs, other users' processes "
                f"using {others / 100:.1f} CPUs")
    return None


def profile(workdir: Path, root: Path = Path("/"), allowed: list[int] | None = None) -> dict:
    """The machine: CPU, L3 domains, kernel, the work dir's filesystem and the CPU set per thread label."""
    model = cpu_model(root)
    if allowed is None:
        allowed = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else \
            list(range(os.cpu_count() or 1))
    domains = l3_domains(root)
    cores = {c: core_of(c, root) for c in allowed}
    sets = cpu_sets(allowed, domains, thread_counts(allowed), cores)
    mountinfo = _read(root / "proc/self/mountinfo") or ""
    return {
        "id": machine_id(model, root),
        "cpu_model": model,
        "logical_cpus": os.cpu_count(),
        "allowed_cpus": format_cpu_list(allowed),
        "l3_domains": domains,
        "cpu_sets": {label: format_cpu_list(cpus) for label, cpus in sets.items()},
        "kernel": platform.release(),
        "python": platform.python_version(),
        "workdir_fs": mount_fs(workdir.resolve(), mountinfo),
    }


class Pinning:
    """The CPU set for each thread count a timed run asks for, chosen once and reused."""

    def __init__(self, allowed: list[int], many: int, root: Path = Path("/")):
        self.allowed = sorted(allowed)
        self.many = many
        self.domains = l3_domains(root)
        self.cores = {c: core_of(c, root) for c in self.allowed}
        self._sets: dict[int, list[int]] = {}

    def cpus(self, threads: int | None) -> list[int]:
        """`None` is a tool's own default threading: it gets the tmax set."""
        n = self.many if threads is None else threads
        if n not in self._sets:
            self._sets[n] = cpu_sets(self.allowed, self.domains, {"n": n}, self.cores)["n"]
        return self._sets[n]

    def labels(self) -> dict[str, str]:
        return {label: format_cpu_list(self.cpus(n)) for label, n in
                (("t1", 1), ("t4", min(4, self.many)), ("tmax", self.many))}

    def wrap(self, cmd: list[str], threads: int | None) -> tuple[list[str], str | None]:
        """cmd under `taskset -c <set>`, and the set; cmd unchanged where taskset is missing."""
        import shutil
        if not shutil.which("taskset"):
            return cmd, None
        cpus = format_cpu_list(self.cpus(threads))
        return ["taskset", "-c", cpus, *cmd], cpus


def avoid_cpu0() -> list[int]:
    """Move this process (and so everything it starts) off CPU 0; returns the CPUs left to it."""
    allowed = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else list(range(os.cpu_count() or 1))
    usable = [c for c in allowed if c != 0] or allowed
    if usable != allowed and hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, usable)
    return usable
