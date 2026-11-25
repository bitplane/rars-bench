"""The timed scenarios: what each one runs, against which reference, and how it is checked.

A scenario is a list of groups. A group is the set of commands that answer one
question side by side, such as "test the 1 GiB member at 4 threads" for rars,
unrar and rarfast. Within a group the commands are interleaved round by round,
in an order that rotates each round, and each one's minimum wall time over
the rounds is kept. Peak RSS is the largest over the rounds. Correctness is
checked once, untimed, on the last round's output: an extracted tree is
compared file by file (size and CRC32) with the input, a created archive is
tested by `unrar t`, and a test is its exit status.

Timing compares only native binaries: rars, `rar` 7.12 (RAR 5 writing),
`rar` 3.93 (RAR 2.9 to 4.x writing), `unrar` 7.20 and rarfast.
"""
from __future__ import annotations

import shutil
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .inputs import Inputs, archive_bytes, compare_tree, first_volume
from .measure import log_tail, measure


@dataclass
class Tools:
    rars: Path
    rar: Path | None
    rar_sha: str
    rar393: Path | None
    unrar: Path | None
    rarfast: Path | None
    many: int

    def readers(self) -> list[str]:
        return ["rars"] + [n for n in ("unrar", "rarfast") if getattr(self, n)]


@dataclass
class Op:
    tool: str                    # rars, rars-in-place, unrar, rarfast, rar, rar393
    kind: str                    # test | extract | create
    cmd: list[str]
    cwd: Path | None = None
    out: Path | None = None      # extract target or the directory a create writes into
    expected: dict | None = None
    threads: int | None = None   # None: the tool's own default
    reference: bool = False


@dataclass
class Group:
    name: str
    ops: list[Op]
    attrs: dict = field(default_factory=dict)   # level, class, threads: for tables


def rars_global(tools: Tools, threads: int | None) -> list[str]:
    cmd = [str(tools.rars), "--progress", "never"]
    return cmd + ["--threads", str(threads)] if threads else cmd


def reader_ops(tools: Tools, kind: str, archive: Path, scratch: Path, threads: int,
               expected: dict | None = None, in_place: bool = True) -> list[Op]:
    """rars, unrar and rarfast reading one archive, at one thread count."""
    ops = []
    if kind == "test":
        ops.append(Op("rars", kind, rars_global(tools, threads) + ["test", str(archive)], threads=threads))
    else:
        variants = [("rars", [])] + ([("rars-in-place", ["--in-place"])] if in_place else [])
        for name, extra in variants:
            out = scratch / f"x-{name}"
            ops.append(Op(name, kind, rars_global(tools, threads) + ["x", *extra, str(archive), str(out)],
                          out=out, expected=expected, threads=threads))
    for name in ("unrar", "rarfast"):
        binary = getattr(tools, name)
        if binary is None:
            continue
        switches = ["-idq", "-p-", f"-mt{threads}"]
        if kind == "test":
            cmd = [str(binary), "t", *switches, str(archive)]
            out = None
        else:
            out = scratch / f"x-{name}"
            cmd = [str(binary), "x", *switches, "-o+", str(archive), f"{out}/"]
        ops.append(Op(name, kind, cmd, out=out, expected=expected, threads=threads,
                      reference=(name == "unrar")))
    return ops


def creator_ops(tools: Tools, scratch: Path, cwd: Path, members: list[str], *, level: int,
                threads: int | None, fmt: str = "rar50", volume: str | None = None,
                legacy: bool = False) -> list[Op]:
    """rars against its native reference writing the same members at the same level."""
    tag = f"{fmt}-m{level}-t{threads or 'd'}{'-v' if volume else ''}"
    ops = []
    out = scratch / f"c-rars-{tag}"
    cmd = rars_global(tools, threads) + ["a", "--format", fmt]
    cmd += ["--store"] if level == 0 else ["--level", str(level)]
    cmd += ["--volume-size", volume] if volume else []
    ops.append(Op("rars", "create", cmd + [str(out / "out.rar"), *members], cwd=cwd, out=out, threads=threads))
    if legacy:
        if tools.rar393 is not None:
            out = scratch / f"c-rar393-{tag}"
            ops.append(Op("rar393", "create", [str(tools.rar393), "a", "-idq", f"-m{level}",
                                               str(out / "out.rar"), *members],
                          cwd=cwd, out=out, threads=None, reference=True))
        return ops
    for name in ("rar", "rarfast"):
        binary = getattr(tools, name)
        if binary is None:
            continue
        out = scratch / f"c-{name}-{tag}"
        switches = ["-idq", f"-m{level}"] + ([f"-mt{threads}"] if threads else [])
        switches += [f"-v{volume}"] if volume else []
        ops.append(Op(name, "create", [str(binary), "a", *switches, str(out / "out.rar"), *members],
                      cwd=cwd, out=out, threads=threads, reference=(name == "rar")))
    return ops


# --- the scenarios -------------------------------------------------------------------------------

def volumes_rar(ctx) -> tuple[dict, list[Group]]:
    """Usenet download: a stored set written by rar 7.12, `-m0 -v<size>`, tested and extracted."""
    payload = ctx.inputs.payload()
    vol = ctx.scale["volume_size"]
    ref = ctx.inputs.reference_archive(f"payload-m0-v{vol}", ctx.tools.rar, ctx.tools.rar_sha,
                                       ["-m0", f"-v{vol}"], Path(payload["path"]), ["payload.bin"])
    expected = ctx.inputs.manifest_of(payload["name"])
    archive = first_volume(ref)
    many = ctx.tools.many
    groups = [Group("test", reader_ops(ctx.tools, "test", archive, ctx.scratch, many), {"threads": many}),
              Group("extract", reader_ops(ctx.tools, "extract", archive, ctx.scratch, many, expected),
                    {"threads": many})]
    return {"input": payload, "archive_bytes": archive_bytes(ref), "volumes": len(list(ref.glob("*.rar"))),
            "made_by": "rar 7.12"}, groups


def volumes_rars(ctx) -> tuple[dict, list[Group]]:
    """Usenet posting and download: writing the stored set, then reading the one rars wrote."""
    payload = ctx.inputs.payload()
    vol = ctx.scale["volume_size"]
    src = Path(payload["path"])
    made = ctx.scratch / "rars-set"
    made.mkdir(parents=True, exist_ok=True)
    subprocess.run(rars_global(ctx.tools, None) + ["a", "--store", "--volume-size", vol,
                                                   str(made / "set.rar"), "payload.bin"],
                   cwd=src, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    expected = ctx.inputs.manifest_of(payload["name"])
    archive = first_volume(made)
    many = ctx.tools.many
    groups = [
        Group("create", creator_ops(ctx.tools, ctx.scratch, src, ["payload.bin"], level=0, threads=many,
                                    volume=vol), {"threads": many, "level": 0}),
        Group("test", reader_ops(ctx.tools, "test", archive, ctx.scratch, many), {"threads": many}),
        Group("extract", reader_ops(ctx.tools, "extract", archive, ctx.scratch, many, expected),
              {"threads": many}),
    ]
    return {"input": payload, "archive_bytes": archive_bytes(made), "volumes": len(list(made.glob("*.rar"))),
            "made_by": "rars (this build)"}, groups


def rar5_big(ctx) -> tuple[dict, list[Group]]:
    """One large compressed RAR 5 member written by rar 7.12 -m3, read at several thread counts."""
    big = ctx.inputs.big()
    ref = ctx.inputs.reference_archive(f"big-m3-{big['bytes']}", ctx.tools.rar, ctx.tools.rar_sha,
                                       ["-m3"], Path(big["path"]), ["usr.tar"])
    expected = ctx.inputs.manifest_of(big["name"])
    archive = first_volume(ref)
    groups = []
    for threads in ctx.scale["read_threads"](ctx.tools.many):
        groups.append(Group(f"test t{threads}", reader_ops(ctx.tools, "test", archive, ctx.scratch, threads),
                            {"threads": threads}))
        groups.append(Group(f"extract t{threads}",
                            reader_ops(ctx.tools, "extract", archive, ctx.scratch, threads, expected,
                                       in_place=False), {"threads": threads}))
    return {"input": big, "archive_bytes": archive_bytes(ref), "made_by": "rar 7.12 -m3"}, groups


def small_files(ctx) -> tuple[dict, list[Group]]:
    """Many files under 64 KiB in one RAR 5 archive by rar 7.12 -m3: staged and in-place extraction."""
    small = ctx.inputs.small_files()
    ref = ctx.inputs.reference_archive(f"small-m3-{small['files']}", ctx.tools.rar, ctx.tools.rar_sha,
                                       ["-m3", "-r"], Path(small["path"]), ["tree"])
    expected = ctx.inputs.manifest_of(small["name"])
    archive = first_volume(ref)
    many = ctx.tools.many
    groups = [Group("test", reader_ops(ctx.tools, "test", archive, ctx.scratch, many), {"threads": many}),
              Group("extract", reader_ops(ctx.tools, "extract", archive, ctx.scratch, many, expected),
                    {"threads": many})]
    return {"input": small, "archive_bytes": archive_bytes(ref), "made_by": "rar 7.12 -m3 -r"}, groups


def create_rar5(ctx) -> tuple[dict, list[Group]]:
    """RAR 5 writing at matched levels and threads, one archive per corpus class."""
    classes = ctx.inputs.classes()
    groups = []
    for threads in ctx.scale["create_threads"](ctx.tools.many):
        for level in ctx.scale["create_levels"]:
            for cls, info in classes.items():
                groups.append(Group(f"{cls} m{level} t{threads}",
                                    creator_ops(ctx.tools, ctx.scratch, Path(info["path"]), [f"{cls}.tar"],
                                                level=level, threads=threads),
                                    {"class": cls, "level": level, "threads": threads}))
    return {"input": classes}, groups


def create_random(ctx) -> tuple[dict, list[Group]]:
    """RAR 5 writing incompressible data, where rars samples instead of coding."""
    rnd = ctx.inputs.random()
    groups = []
    for threads, levels in ctx.scale["random_cells"](ctx.tools.many):
        for level in levels:
            groups.append(Group(f"random m{level} t{threads}",
                                creator_ops(ctx.tools, ctx.scratch, Path(rnd["path"]), ["random.bin"],
                                            level=level, threads=threads),
                                {"class": "random", "level": level, "threads": threads}))
    return {"input": rnd}, groups


def create_rar4(ctx) -> tuple[dict, list[Group]]:
    """RAR 2.9-4.x writing (`--format rar40`) against native rar 3.93, which takes no thread switch.

    rar 7.12 can no longer write RAR 4 (`-ma4` is refused), and neither can
    rarfast 0.9.0, so 3.93 is the only native reference for this format.
    """
    classes = ctx.inputs.classes()
    groups = []
    for threads in ctx.scale["create_threads"](ctx.tools.many):
        for cls, info in classes.items():
            groups.append(Group(f"{cls} m3 t{threads}",
                                creator_ops(ctx.tools, ctx.scratch, Path(info["path"]), [f"{cls}.tar"],
                                            level=3, threads=threads, fmt="rar40", legacy=True),
                                {"class": cls, "level": 3, "threads": threads}))
    return {"input": classes, "note": "rar 3.93 runs at its own default threading in every group"}, groups


SCENARIOS = {
    "volumes-rar": volumes_rar,
    "volumes-rars": volumes_rars,
    "rar5-big": rar5_big,
    "small-files": small_files,
    "create-rar5": create_rar5,
    "create-random": create_random,
    "create-rar4": create_rar4,
}


# --- running a group -----------------------------------------------------------------------------

def run_group(group: Group, ctx) -> list[dict]:
    rounds = ctx.rounds
    runs: dict[int, list] = {i: [] for i in range(len(group.ops))}
    done: set[int] = set()
    results: dict[int, dict] = {}
    logs = ctx.scratch / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for round_ in range(rounds):
        shift = round_ % len(group.ops)
        order = list(range(len(group.ops)))[shift:] + list(range(len(group.ops)))[:shift]
        for i in order:
            if i in done:
                continue
            op = group.ops[i]
            if op.out is not None:
                shutil.rmtree(op.out, ignore_errors=True)
                op.out.mkdir(parents=True)
            log = logs / f"{i}.log"
            cmd, cpus = ctx.pinning.wrap(op.cmd, op.threads) if ctx.pinning else (op.cmd, None)
            m = measure(cmd, cwd=op.cwd, log=log, timeout=ctx.timeout)
            runs[i].append(m)
            last = round_ == rounds - 1
            if m.exit != 0 or last:
                results[i] = {**_finish(op, runs[i], log, ctx), "cpus": cpus}
                done.add(i)
    return [results[i] for i in range(len(group.ops))]


def _finish(op: Op, runs: list, log: Path, ctx) -> dict:
    final = runs[-1]
    ok, detail, out_bytes = final.exit == 0, "", None
    if final.timed_out:
        ok, detail = False, f"timed out after {ctx.timeout:.0f}s"
    elif final.exit != 0:
        detail = f"exit {final.exit}: {log_tail(log)}"
    elif op.kind == "extract":
        ok, detail = compare_tree(op.out, op.expected)
    elif op.kind == "create":
        out_bytes = archive_bytes(op.out)
        if ctx.tools.unrar is None:
            detail = "not checked: no unrar"
        else:
            check = subprocess.run([str(ctx.tools.unrar), "t", "-idq", "-p-", str(first_volume(op.out))],
                                   capture_output=True, text=True, stdin=subprocess.DEVNULL)
            ok = check.returncode == 0
            detail = "unrar t ok" if ok else f"unrar t exit {check.returncode}: {check.stdout[-300:]}"
    else:
        detail = "exit 0"
    if op.out is not None:
        shutil.rmtree(op.out, ignore_errors=True)
    best = min(runs, key=lambda m: m.wall_s)
    clean = [m for m in runs if m.exit == 0] or runs
    return {
        "tool": op.tool, "kind": op.kind, "threads": op.threads, "reference": op.reference,
        "cmd": " ".join(op.cmd), "rounds": len(runs),
        "wall_s": [round(m.wall_s, 4) for m in runs],
        "wall_min_s": round(min(m.wall_s for m in clean), 4),
        "wall_median_s": round(statistics.median(m.wall_s for m in clean), 4),
        "cpu_s_at_min": round(best.cpu_s, 4),
        "rss_max_kib": max((m.rss_kib or 0 for m in runs), default=0) or None,
        "out_bytes": out_bytes, "ok": ok, "detail": detail,
    }
