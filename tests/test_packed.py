"""packed_bytes: the members' packed sizes, read from block headers.

The synthetic archives here are built field by field from the published
header layouts; CRCs are left zero because the walk never checks them. The
rars-backed tests check the walk against `rars info` on real archives of
every format rars writes.
"""
import re
import sqlite3
import struct
import subprocess
from pathlib import Path

import pytest

from rars_bench.drivers import RarsDriver
from rars_bench.packed import SIG14, SIG15, SIG50, backfill, packed_bytes
from rars_bench.schema import connect_read, init_db
from rars_bench import triage
from rars_bench.variants import Variant


# --- synthetic archives ------------------------------------------------------

def rar14(members: list[tuple[str, bytes]], comment: bytes = b"") -> bytes:
    main = SIG14 + struct.pack("<HB", 7 + len(comment), 0x02 if comment else 0) + comment
    out = main
    for name, packed in members:
        raw = name.encode()
        head_size = 21 + len(raw)
        out += struct.pack("<IIHHIBBBBB", len(packed), len(packed) * 3, 0, head_size,
                           0, 0x20, 0, 15, len(raw), 0x33) + raw + packed
    return out


def _block15(kind: int, flags: int, body: bytes, data: bytes = b"") -> bytes:
    if flags & 0x8000:
        head = struct.pack("<HBHHI", 0, kind, flags, 11 + len(body), len(data))
    else:
        head = struct.pack("<HBHH", 0, kind, flags, 7 + len(body))
    return head + body + data


def rar15(members: list[tuple[str, bytes]], *, main_flags: int = 0, end: bool = True) -> bytes:
    out = SIG15 + _block15(0x73, main_flags, b"\0" * 6)
    for name, packed in members:
        raw = name.encode()
        # unp(4) host(1) crc(4) time(4) unp_ver(1) method(1) name_size(2) attr(4), then the name
        body = struct.pack("<IBIIBBHI", len(packed) * 3, 2, 0, 0, 20, 0x33, len(raw), 0x20) + raw
        out += _block15(0x74, 0x8000, body, packed)
    # A sub-block with its own data, the shape of rars's producer marker.
    out += _block15(0x77, 0x8000, b"\0" * 3, b"RARS" + b"\0" * 20)
    if end:
        out += _block15(0x7B, 0x4000, b"")
    return out


def _vint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _block50(kind: int, fields: bytes = b"", data: bytes = b"", extra: bytes = b"") -> bytes:
    flags = (0x01 if extra else 0) | (0x02 if data else 0)
    header = _vint(kind) + _vint(flags)
    if extra:
        header += _vint(len(extra))
    if data:
        header += _vint(len(data))
    header += fields + extra
    return struct.pack("<I", 0) + _vint(len(header)) + header + data


def rar50(members: list[tuple[str, bytes]], *, encrypted: bool = False) -> bytes:
    out = SIG50
    if encrypted:
        out += _block50(4, b"\0" * 4)
    out += _block50(1, _vint(0))
    for name, packed in members:
        raw = name.encode()
        fields = _vint(0) + _vint(len(packed) * 3) + _vint(0x20) + _vint(0) + _vint(0) \
            + _vint(len(raw)) + raw
        out += _block50(2, fields, packed)
    out += _block50(3, _vint(0) + _vint(0) + _vint(0) + _vint(0) + _vint(0) + _vint(2) + b"QO",
                    b"q" * 40)
    out += _block50(5, _vint(0))
    return out


MEMBERS = [("alternatives.log", b"x" * 520), ("history.log", b"y" * 1896), ("empty", b"")]
PACKED = 520 + 1896


@pytest.mark.parametrize("build", [rar14, rar15, rar50])
def test_sums_member_data_only(build, tmp_path):
    archive = tmp_path / "a.rar"
    archive.write_bytes(build(MEMBERS))
    assert packed_bytes(archive) == PACKED


@pytest.mark.parametrize("build", [rar14, rar15, rar50])
def test_names_do_not_change_the_sum(build, tmp_path):
    short, long = tmp_path / "short.rar", tmp_path / "long.rar"
    short.write_bytes(build([("ALTERNAT.LOG", b"x" * 520)]))
    long.write_bytes(build([("corpora/logs/alternatives.log", b"x" * 520)]))
    assert short.stat().st_size != long.stat().st_size
    assert packed_bytes(short) == packed_bytes(long) == 520


def test_rar14_main_comment_is_skipped(tmp_path):
    archive = tmp_path / "a.rar"
    archive.write_bytes(rar14(MEMBERS, comment=b"\x05\x00hello"))
    assert packed_bytes(archive) == PACKED


@pytest.mark.parametrize("build", [rar14, rar15, rar50])
def test_truncated_archive_has_no_size(build, tmp_path):
    whole = build(MEMBERS)
    archive = tmp_path / "a.rar"
    for cut in (7, len(whole) // 2, len(whole) - 1):
        archive.write_bytes(whole[:cut])
        assert packed_bytes(archive) is None, cut


def test_rar15_without_end_block_still_counts(tmp_path):
    archive = tmp_path / "a.rar"
    archive.write_bytes(rar15(MEMBERS, end=False))
    assert packed_bytes(archive) == PACKED


def test_encrypted_headers_have_no_size(tmp_path):
    a15, a50 = tmp_path / "a15.rar", tmp_path / "a50.rar"
    a15.write_bytes(rar15(MEMBERS, main_flags=0x0080))
    a50.write_bytes(rar50(MEMBERS, encrypted=True))
    assert packed_bytes(a15) is None
    assert packed_bytes(a50) is None


def test_not_an_archive_has_no_size(tmp_path):
    other = tmp_path / "a.zip"
    other.write_bytes(b"PK\x03\x04" + b"\0" * 40)
    assert packed_bytes(other) is None


# --- schema migration, backfill, triage ---------------------------------------

OLD_SCHEMA = """
CREATE TABLE runs (
    run_id TEXT NOT NULL, ts TEXT NOT NULL, format TEXT NOT NULL, method INTEGER,
    filter TEXT, solid INTEGER, encrypted INTEGER, dict_log INTEGER,
    corpus_class TEXT NOT NULL, corpus_path TEXT NOT NULL, input_bytes INTEGER NOT NULL,
    encoder TEXT NOT NULL, encoder_version TEXT NOT NULL, output_bytes INTEGER,
    encode_ms REAL, encode_peak_rss INTEGER, decode_ms REAL, decode_peak_rss INTEGER,
    roundtrip_status TEXT, rars_internal TEXT
);
"""

VARIANT = Variant(format="rar20", method="lz-5", method_int=5, filter="none", solid=False,
                  encrypted=False, password=None, dict_log=None, volume_size=None)
KEYS = {("rar20", 5, "none", 0, 0, None): VARIANT.key}


def _old_db(path: Path, rows: list[tuple[str, int]]) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    for encoder, size in rows:
        conn.execute(
            "INSERT INTO runs VALUES ('run1', 'ts', 'rar20', 5, 'none', 0, 0, NULL, 'logs', 'logs',"
            " 31000, ?, 'v', ?, 1.0, NULL, 1.0, NULL, 'ok', NULL)",
            (encoder, size),
        )
    conn.commit()
    conn.close()


def _columns(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    finally:
        conn.close()


def test_init_db_adds_packed_bytes_to_an_old_db(tmp_path):
    db = tmp_path / "old.db"
    _old_db(db, [("rars", 100)])
    init_db(db).close()
    assert "packed_bytes" in _columns(db)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT output_bytes, packed_bytes FROM runs").fetchall() == [(100, None)]
    conn.close()


def test_connect_read_leaves_an_old_db_alone(tmp_path):
    db = tmp_path / "old.db"
    _old_db(db, [("rars", 100)])
    conn = connect_read(db)
    assert conn.execute("SELECT packed_bytes FROM runs").fetchall() == [(None,)]
    conn.close()
    assert "packed_bytes" not in _columns(db)


def test_triage_reads_an_old_db(tmp_path, capsys):
    db = tmp_path / "old.db"
    _old_db(db, [("rars", 110), ("rar", 100)])
    assert triage.run(db, "ratio-gap", 5) == 0
    out = capsys.readouterr().out
    assert "packed_gap" in out and "110" in out


def test_backfill_fills_rows_whose_archive_is_still_there(tmp_path):
    rars_archive, ref_archive = rar15(MEMBERS), rar15([("A.LOG", b"x" * 700)])
    db = tmp_path / "old.db"
    # The reference row's archive has since been replaced by one of another size.
    _old_db(db, [("rars", len(rars_archive)), ("rar", len(ref_archive) + 1)])
    for encoder, data in (("rars", rars_archive), ("rar", ref_archive)):
        cell = tmp_path / "work" / "run1" / encoder / VARIANT.key / "logs"
        cell.mkdir(parents=True)
        (cell / "out.rar").write_bytes(data)
    assert backfill(db, tmp_path / "work", KEYS) == (1, 1)
    conn = sqlite3.connect(db)
    got = dict(conn.execute("SELECT encoder, packed_bytes FROM runs"))
    conn.close()
    assert got == {"rars": PACKED, "rar": None}


# --- against real archives -----------------------------------------------------

def _info_packed(rars_bin: Path, archive: Path) -> tuple[int, list[str]]:
    out = subprocess.run([str(rars_bin), "info", str(archive)], capture_output=True, text=True,
                         check=True).stdout
    rows = re.findall(r"^\s+(\d+)\s+(\d+)\s+(.+)$", out, re.MULTILINE)
    return sum(int(packed) for _, packed, _ in rows), [name for _, _, name in rows]


FORMATS = ["rar14", "rar15", "rar20", "rar29", "rar30", "rar40", "rar50", "rar70"]


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("method,level", [("stored", 0), ("lz-3", 3)])
def test_matches_rars_info(rars_bin, tiny_corpus, tmp_path, fmt, method, level):
    variant = Variant(format=fmt, method=method, method_int=level, filter="none", solid=False,
                      encrypted=False, password=None, dict_log=None, volume_size=None)
    inputs = sorted(p for p in (tiny_corpus / "text-small").iterdir() if p.stat().st_size)
    archive = tmp_path / "out.rar"
    enc = RarsDriver(rars_bin).compress(variant, inputs, archive)
    assert enc.exit_code == 0, enc.stderr
    expected, names = _info_packed(rars_bin, archive)
    assert packed_bytes(archive) == expected
    assert packed_bytes(archive) < enc.archive_bytes
    # The driver runs rars from the inputs' directory, so members carry bare
    # names the way the references store them, not the harness's path.
    assert names == [p.name for p in inputs]
