"""The edge-case generators: same bytes every time, and the shapes they claim."""
import hashlib
import struct
import zlib
from pathlib import Path

from rars_bench import synth
from rars_bench.harness import _resolve_corpus_classes


def h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def test_bytes_are_pinned() -> None:
    # A change here changes every oracle-cache key for the edge classes: bump on purpose only.
    assert h(synth.pcm(4096, 8, 1, 0x6D380001)) == "afa5fd368cda3be3"
    assert h(synth.pcm(4096, 8, 2, 0x73380001)) == "ba86c5467af4987f"
    assert h(synth.pcm(4096, 16, 1, 0x6D310001)) == "4da393fc017994f8"
    assert h(synth.pcm(4096, 16, 2, 0x61700001)) == "4e5a7230d204a0b7"
    assert h(synth.rgb24(64, 16, 0x72670001)) == "1886216c3a0dd6fe"
    assert h(synth.grey(64, 16, 0x67720001)) == "2b455041468f32f7"


def test_integer_sine() -> None:
    assert synth.isin(0, 400, 1000) == 0
    assert synth.isin(100, 400, 1000) == 1000
    assert synth.isin(300, 400, 1000) == -1000
    assert abs(synth.isin(50, 400, 1000) - 707) < 5


def test_samples_code_smaller_as_deltas() -> None:
    mono = synth.pcm(1 << 16, 8, 1, 1)
    delta = bytes((mono[i] - mono[i - 1]) & 0xFF for i in range(1, len(mono)))
    assert len(zlib.compress(delta, 9)) < 0.8 * len(zlib.compress(mono, 9))
    stereo = struct.unpack("<%dh" % (1 << 14), synth.pcm(1 << 15, 16, 2, 1))
    left = stereo[0::2]
    assert max(left) > 8000 and min(left) < -8000


def test_seed_edge_writes_classes(tmp_path: Path) -> None:
    only = ["edge-records", "edge-repeats", "edge-random"]
    digests = synth.seed_edge(tmp_path, only)
    assert {k: v[:16] for k, v in digests.items()} == {
        "edge-records": "7adc9369fe3d6631", "edge-repeats": "719f0f1bd3b83893", "edge-random": "c20d458d439488a0"}
    for cls in only:
        files = list((tmp_path / cls).iterdir())
        assert len(files) == 1 and files[0].stat().st_size <= 4 << 20
    # The period is about 8 KiB: the file repeats itself exactly at it.
    data = (tmp_path / "edge-repeats" / "repeats.bin").read_bytes()
    assert data[:4096] == data[8199:8199 + 4096] and bytes(16) in data[:8199]


def test_selectors_keep_edge_out_of_small(tmp_path: Path) -> None:
    for cls in ("logs", "large-text", "edge-random"):
        (tmp_path / cls).mkdir()
    assert _resolve_corpus_classes(["small"], tmp_path) == ["logs"]
    assert _resolve_corpus_classes(["edge"], tmp_path) == ["edge-random"]
    assert _resolve_corpus_classes(["all"], tmp_path) == ["edge-random", "large-text", "logs"]
