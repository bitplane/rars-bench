"""Roundtrip predicates — pure functions over driver result objects.

The cross-validation strategy is `rar t` (test mode): the decoder
decompresses every member, verifies stored CRCs against the decompressed
bytes, and returns 0/non-0 — without writing to disk. This sidesteps
extraction artifacts (8.3 name collisions on DOS RAR overwriting members,
filesystem permission quirks under wine) that previously masked or invented
roundtrip failures. As a consequence we no longer distinguish 'wrong_bytes'
from 'decode_failed' in the schema — both now collapse to 'decode_failed'.
"""
from .drivers.base import EncodeResult


def roundtrip_status(encode: EncodeResult, test_exit_code: int) -> str:
    """Resolve schema status from an encode result + cross-test exit code.

    Returns one of: 'ok' | 'encode_failed' | 'decode_failed'.
    """
    if encode.exit_code != 0:
        return "encode_failed"
    if not encode.archive_path.exists() or encode.archive_bytes <= 0:
        return "encode_failed"
    if test_exit_code != 0:
        return "decode_failed"
    return "ok"
