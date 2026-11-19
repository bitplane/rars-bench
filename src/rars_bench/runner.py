"""Sweep runner — for each (variant, corpus_class, encoder) pair, build ONE
multi-member archive containing every file in corpora/<corpus_class>/, then
cross-decode through the other encoder, and persist a row.

Schema convention: the schema has no `decoder` column, so we record the
cross-decoder's identity implicitly as "the other encoder in the available
list". corpus_path stores the corpus_class name (since the archive scope
is the whole class), input_bytes is the sum across members.

output_bytes is the whole archive and packed_bytes the members' compressed
data alone (see packed.py). Score encoders on packed_bytes: the archive also
carries each side's member names, which the two sides store differently.

Sweep is serial — RUSAGE_CHILDREN deltas are only meaningful when there's
exactly one outstanding child at a time.
"""
import datetime as _dt
import time
import uuid
from pathlib import Path

from . import cache as _cache
from .compare import roundtrip_status
from .corpus import CorpusItem, enumerate_class
from .drivers.base import Driver, EncodeResult
from .packed import packed_bytes
from .schema import init_db
from .variants import Variant


_INSERT_SQL = """
INSERT INTO runs (
    run_id, ts, format, method, filter, solid, encrypted, dict_log,
    corpus_class, corpus_path, input_bytes,
    encoder, encoder_version, output_bytes,
    encode_ms, encode_peak_rss, decode_ms, decode_peak_rss,
    roundtrip_status, rars_internal, packed_bytes
) VALUES (
    ?, ?, ?, ?, ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?
)
"""


def sweep(
    *,
    db_path: Path,
    corpus_root: Path,
    corpus_classes: list[str],
    variants: list[Variant],
    drivers: list[Driver],
    work_root: Path,
    cache_root: Path | None = None,
    run_id: str | None = None,
    skip_unavailable: bool = False,
) -> int:
    """Run each variant × corpus_class × encoder, write rows, return row count.

    For each (variant, corpus_class, encoder), the encoder produces ONE archive
    containing every file in corpora/<corpus_class>/ (multi-member). The
    cross-decoder is the next available driver in the list that isn't itself;
    if none, we self-decode.
    """
    available_drivers = [d for d in drivers if d.available()]
    if not available_drivers:
        if skip_unavailable:
            return 0
        raise RuntimeError("no drivers available")
    if not skip_unavailable and len(available_drivers) != len(drivers):
        missing = [d.name for d in drivers if not d.available()]
        raise RuntimeError(f"drivers unavailable: {missing}")

    run_id = run_id or uuid.uuid4().hex[:12]
    work_root = work_root / run_id
    work_root.mkdir(parents=True, exist_ok=True)

    conn = init_db(db_path)
    try:
        rows_written = 0
        for variant in variants:
            for corpus_class in corpus_classes:
                items: list[CorpusItem] = enumerate_class(corpus_root, corpus_class)
                if not items:
                    continue
                expected_shas = {item.sha256 for item in items}
                input_bytes = sum(item.size for item in items)
                inputs = [item.abs_path for item in items]

                input_shas = [item.sha256 for item in items]
                for encoder in available_drivers:
                    decoder = _pick_decoder(encoder, available_drivers)
                    enc, test_exit, test_ms = _run_cell(
                        encoder=encoder,
                        decoder=decoder,
                        variant=variant,
                        corpus_class=corpus_class,
                        inputs=inputs,
                        input_shas=input_shas,
                        work_root=work_root,
                        cache_root=cache_root,
                    )
                    status = roundtrip_status(enc, test_exit)
                    packed = (
                        packed_bytes(enc.archive_path)
                        if enc.archive_bytes > 0 and enc.archive_path.is_file()
                        else None
                    )
                    conn.execute(
                        _INSERT_SQL,
                        (
                            run_id,
                            _now_iso(),
                            variant.format,
                            variant.method_int,
                            variant.filter,
                            int(variant.solid),
                            int(variant.encrypted),
                            variant.dict_log,
                            corpus_class,
                            corpus_class,        # corpus_path == class for multi-member archives
                            input_bytes,
                            encoder.name,
                            encoder.version,
                            enc.archive_bytes,
                            enc.encode_ms,
                            enc.peak_rss_kib,
                            test_ms,
                            None,
                            status,
                            None,
                            packed,
                        ),
                    )
                    rows_written += 1
        conn.commit()
        return rows_written
    finally:
        conn.close()


def _pick_decoder(encoder: Driver, drivers: list[Driver]) -> Driver:
    for d in drivers:
        if d.name != encoder.name:
            return d
    return encoder


def _run_cell(
    *,
    encoder: Driver,
    decoder: Driver,
    variant: Variant,
    corpus_class: str,
    inputs: list[Path],
    input_shas: list[str],
    work_root: Path,
    cache_root: Path | None,
) -> tuple[EncodeResult, int, float]:
    """Encode the corpus, then cross-test via decoder.test (NOT extract).

    Returns (encode result, test exit code, test elapsed ms). Test mode
    avoids extraction artifacts; see compare.py for the rationale.
    """
    cell_dir = work_root / encoder.name / variant.key / corpus_class
    archive_path = cell_dir / "out.rar"
    cell_dir.mkdir(parents=True, exist_ok=True)

    enc: EncodeResult | None = None
    use_cache = cache_root is not None and getattr(encoder, "cacheable", False)
    if use_cache:
        enc = _cache.lookup(cache_root, encoder, variant, corpus_class, input_shas, archive_path)
    if enc is None:
        enc = encoder.compress(variant, inputs, archive_path)
        if use_cache:
            _cache.store(cache_root, encoder, variant, corpus_class, input_shas, archive_path, enc)
    if enc.exit_code != 0 or not archive_path.is_file() or enc.archive_bytes <= 0:
        return enc, -1, 0.0

    t0 = time.monotonic()
    test_exit = decoder.test(variant, archive_path, password=variant.password)
    test_ms = (time.monotonic() - t0) * 1000.0
    return enc, test_exit, test_ms


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat(timespec="seconds")
