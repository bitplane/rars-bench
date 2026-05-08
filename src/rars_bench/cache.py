"""Encode-result cache keyed on (driver, variant, corpus, input shas).

Wine and DOSBox-X encodes are deterministic for a fixed (binary, inputs,
variant), so re-running them every sweep is wasted minutes. The rars
encoder is intentionally NOT cached — its binary churns every build and
its encode timings are the thing under test.

Layout: `<cache_root>/<key>/{out.rar, meta.json}`. `meta.json` carries
encode_ms/peak_rss_kib/archive_bytes so cached cells produce comparable
schema rows. The driver's `version` field is part of the key, so binary
changes (e.g. replacing wineprefix RAR.EXE) auto-invalidate.
"""
import hashlib
import json
import shutil
from pathlib import Path

from .drivers.base import Driver, EncodeResult
from .variants import Variant


def cache_key(
    encoder: Driver,
    variant: Variant,
    corpus_class: str,
    input_shas: list[str],
) -> str:
    h = hashlib.sha256()
    h.update(f"{encoder.name}|{encoder.version}|".encode())
    h.update(f"{variant.format}|{variant.method}|{variant.method_int}|".encode())
    h.update(f"{variant.filter}|{int(variant.solid)}|{int(variant.encrypted)}|".encode())
    h.update(f"{variant.dict_log}|{variant.password or ''}|".encode())
    h.update(f"{variant.volume_size or ''}|".encode())
    h.update(f"{corpus_class}|".encode())
    for sha in sorted(input_shas):
        h.update(f"{sha}|".encode())
    return h.hexdigest()[:24]


def lookup(
    cache_root: Path,
    encoder: Driver,
    variant: Variant,
    corpus_class: str,
    input_shas: list[str],
    dest_archive: Path,
) -> EncodeResult | None:
    key = cache_key(encoder, variant, corpus_class, input_shas)
    cell = cache_root / key
    archive_src = cell / "out.rar"
    meta_path = cell / "meta.json"
    if not (archive_src.is_file() and meta_path.is_file()):
        return None
    try:
        meta = json.loads(meta_path.read_text())
    except (OSError, ValueError):
        return None
    dest_archive.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive_src, dest_archive)
    return EncodeResult(
        archive_path=dest_archive,
        archive_bytes=meta["archive_bytes"],
        exit_code=0,
        encode_ms=float(meta["encode_ms"]),
        peak_rss_kib=meta.get("peak_rss_kib"),
        stdout="",
        stderr="(cache hit)",
    )


def store(
    cache_root: Path,
    encoder: Driver,
    variant: Variant,
    corpus_class: str,
    input_shas: list[str],
    archive_path: Path,
    enc: EncodeResult,
) -> None:
    if enc.exit_code != 0 or not archive_path.is_file() or enc.archive_bytes <= 0:
        return
    key = cache_key(encoder, variant, corpus_class, input_shas)
    cell = cache_root / key
    cell.mkdir(parents=True, exist_ok=True)
    shutil.copy2(archive_path, cell / "out.rar")
    (cell / "meta.json").write_text(
        json.dumps(
            {
                "archive_bytes": enc.archive_bytes,
                "encode_ms": enc.encode_ms,
                "peak_rss_kib": enc.peak_rss_kib,
            }
        )
    )
