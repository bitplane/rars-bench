"""Sum of the members' packed sizes, read from an archive's block headers.

`output_bytes` is the whole archive, so it carries everything that is not
compressed data: the marker and main header, one file header per member with
its name, and any service blocks a writer adds. Two encoders that store names
differently differ there by more than their compressors do on the small tier.
The DOS references store bare 8.3 names, rars stores what it was given, and
rars adds a producer-marker block. Summing the data area of each file block
takes all of that out.

The walk reads headers only and never decompresses, so it needs no wine, no
DOSBox and no decoder, and works the same on either side's archive. It covers
the three header layouts the bench writes: RAR 1.4, RAR 1.5 to 4.x, and
RAR 5.0 and later. Anything it cannot account for byte for byte gives None
rather than a number: a truncated archive (the DOS references leave one when
DOSBox times out), encrypted headers, or an unknown signature.
"""
from __future__ import annotations

import struct
from pathlib import Path

SIG14 = b"RE~^"
SIG15 = b"Rar!\x1a\x07\x00"
SIG50 = b"Rar!\x1a\x07\x01\x00"

# RAR 1.5-4.x block types and flags.
_HEAD_MAIN = 0x73
_HEAD_FILE = 0x74
_HEAD_END = 0x7B
_MHD_PASSWORD = 0x0080       # main header flag: block headers are encrypted
_LHD_LARGE = 0x0100          # file header flag: 64-bit sizes follow the name size
_LONG_BLOCK = 0x8000         # block flag: a 4-byte data size follows the header size

# RAR 5 block types and flags.
_HEAD5_FILE = 2
_HEAD5_CRYPT = 4
_HEAD5_END = 5
_HFL_EXTRA = 0x0001
_HFL_DATA = 0x0002


class _Unaccounted(Exception):
    """The headers do not describe the file exactly."""


def packed_bytes(path: Path) -> int | None:
    """Sum of every member's packed size, or None if the headers don't add up."""
    data = Path(path).read_bytes()
    try:
        if data.startswith(SIG50):
            members = _walk50(data)
        elif data.startswith(SIG15):
            members = _walk15(data)
        elif data.startswith(SIG14):
            members = _walk14(data)
        else:
            return None
    except (_Unaccounted, struct.error, IndexError):
        return None
    return sum(members) if members else None


def _walk14(data: bytes) -> list[int]:
    # Main header: marker(4) head_size(2) flags(1); head_size includes any comment.
    head_size = struct.unpack_from("<H", data, 4)[0]
    pos = _advance(data, 0, head_size, minimum=7)
    members = []
    while pos < len(data):
        # File header: pack(4) unp(4) crc(2) head_size(2) time(4) attr(1)
        # flags(1) unp_ver(1) name_size(1) method(1), then the name.
        pack, _unp, _crc, head_size = struct.unpack_from("<IIHH", data, pos)
        pos = _advance(data, pos, head_size + pack, minimum=21)
        members.append(pack)
    return members


def _walk15(data: bytes) -> list[int]:
    pos = len(SIG15)
    members = []
    while pos < len(data):
        _crc, kind, flags, head_size = struct.unpack_from("<HBHH", data, pos)
        if kind == _HEAD_MAIN and flags & _MHD_PASSWORD:
            raise _Unaccounted("encrypted headers")
        add_size = 0
        if flags & _LONG_BLOCK or kind == _HEAD_FILE:
            add_size = struct.unpack_from("<I", data, pos + 7)[0]
        if kind == _HEAD_FILE and flags & _LHD_LARGE:
            add_size |= struct.unpack_from("<I", data, pos + 32)[0] << 32
        start = pos
        pos = _advance(data, pos, head_size + add_size, minimum=7)
        if kind == _HEAD_FILE:
            members.append(add_size)
        if kind == _HEAD_END:
            # Anything after the end block (a recovery record in a later
            # volume, junk) is not part of this archive's members.
            return members
        if pos <= start:
            raise _Unaccounted("block does not advance")
    return members


def _walk50(data: bytes) -> list[int]:
    pos = len(SIG50)
    members = []
    while pos < len(data):
        start = pos
        header_size, after_size = _vint(data, pos + 4)
        header_end = after_size + header_size
        if header_size == 0 or header_end > len(data):
            raise _Unaccounted("header runs past the end")
        kind, cur = _vint(data, after_size)
        flags, cur = _vint(data, cur)
        if flags & _HFL_EXTRA:
            _, cur = _vint(data, cur)
        data_size = 0
        if flags & _HFL_DATA:
            data_size, cur = _vint(data, cur)
        if cur > header_end:
            raise _Unaccounted("header fields run past the header")
        if kind == _HEAD5_CRYPT:
            raise _Unaccounted("encrypted headers")
        pos = _advance(data, header_end, data_size, minimum=0)
        if kind == _HEAD5_FILE:
            members.append(data_size)
        if kind == _HEAD5_END:
            return members
        if pos <= start:
            raise _Unaccounted("block does not advance")
    return members


def _advance(data: bytes, pos: int, size: int, *, minimum: int) -> int:
    if size < minimum:
        raise _Unaccounted("block shorter than its fixed fields")
    end = pos + size
    if end > len(data):
        raise _Unaccounted("block runs past the end of the file")
    return end


def _vint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    for i in range(10):
        byte = data[pos + i]
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos + i + 1
        shift += 7
    raise _Unaccounted("vint longer than ten bytes")


def backfill(db_path: Path, work_root: Path, variant_keys: dict[tuple, str]) -> tuple[int, int]:
    """Fill packed_bytes on rows written before the column existed.

    A row's archive is still at `<work_root>/<run_id>/<encoder>/<variant
    key>/<class>/out.rar` if nobody has cleaned the work tree. `variant_keys`
    maps a row's (format, method, filter, solid, encrypted, dict_log) to the
    variant key that names that directory. An archive whose size is not the
    row's output_bytes belongs to some other run and is left alone, as is a
    row whose archive is gone. Returns (rows filled, rows still NULL).
    """
    from .schema import init_db

    conn = init_db(db_path)
    try:
        rows = conn.execute(
            "SELECT rowid, run_id, encoder, format, method, filter, solid, encrypted,"
            " dict_log, corpus_class, output_bytes FROM runs WHERE packed_bytes IS NULL"
        ).fetchall()
        filled = 0
        for rowid, run_id, encoder, fmt, method, filt, solid, enc, dict_log, cls, size in rows:
            key = variant_keys.get((fmt, method, filt, solid, enc, dict_log))
            if key is None or not size:
                continue
            archive = work_root / run_id / encoder / key / cls / "out.rar"
            if not archive.is_file() or archive.stat().st_size != size:
                continue
            packed = packed_bytes(archive)
            if packed is None:
                continue
            conn.execute("UPDATE runs SET packed_bytes = ? WHERE rowid = ?", (packed, rowid))
            filled += 1
        conn.commit()
        return filled, len(rows) - filled
    finally:
        conn.close()
