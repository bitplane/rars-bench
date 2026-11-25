"""Seeded synthetic inputs for the shapes rars has special handling for: `corpora/edge-*`.

Each class is one file, at most 4 MiB, built from integers only (an xorshift
noise source and an integer sine), so the bytes are the same on every
machine and Python version and the oracle cache stays valid. The shapes
follow the generators in rars' own tests and issue reproducers (noisy mono
and stereo PCM, noisy RGB ramps, a 16-byte record table, long repeats);
none of their code is used.

    edge-pcm8-mono     8-bit unsigned mono, two tones and a few steps of noise
    edge-pcm8-stereo   the same, two channels interleaved
    edge-pcm16-mono    16-bit signed little-endian, tones plus 8 bits of noise
    edge-pcm16-stereo  the same, two channels interleaved
    edge-rgb24         24-bit pixels, 1024 a row: red and green ramps with noise, blue a clean diagonal
    edge-grey          8-bit greyscale gradient with noise, 1024 a row
    edge-records       a table of 16-byte little-endian records
    edge-repeats       an 8 KiB period, with runs of zeros, repeated
    edge-random        seeded random bytes
"""
from __future__ import annotations

import hashlib
import random
import struct
from pathlib import Path

MIB = 1 << 20


class XorShift:
    def __init__(self, seed: int):
        self.state = seed & 0xFFFFFFFF or 1

    def next(self) -> int:
        s = self.state
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        self.state = s
        return s


def isin(step: int, period: int, amplitude: int) -> int:
    """amplitude * sin(2 pi step / period), by Bhaskara's approximation, in integers."""
    half = period // 2
    x = step % period
    sign = 1
    if x >= half:
        x, sign = x - half, -1
    num = 16 * x * (half - x)
    den = 5 * half * half - 4 * x * (half - x)
    return sign * amplitude * num // den


def pcm(nbytes: int, bits: int, channels: int, seed: int) -> bytes:
    """Two tones a channel, the second channel's a fifth above, plus noise in the low bits."""
    rng = XorShift(seed)
    out = bytearray()
    step = 0
    tones = [(483, 3700), (322, 1000)] if bits == 16 else [(483, 90), (131, 20)]
    while len(out) < nbytes:
        step += 1
        for ch in range(channels):
            value = sum(isin(step * (2 + ch) // 2, period, amp) for period, amp in tones)
            if bits == 16:
                sample = value * (12 if ch == 0 else 9) // 4 + (rng.next() & 0xFF) - 128
                out += struct.pack("<h", max(-32768, min(32767, sample)))
            else:
                sample = 128 + value + rng.next() % 9 - 4
                out.append(max(0, min(255, sample)))
    return bytes(out[:nbytes])


def rgb24(width: int, height: int, seed: int) -> bytes:
    rng = XorShift(seed)
    out = bytearray()
    for y in range(height):
        for x in range(width):
            out += bytes(((x + rng.next() % 4) & 0xFF, (y // 2 + rng.next() % 4) & 0xFF, ((x + y) // 3) & 0xFF))
    return bytes(out)


def grey(width: int, height: int, seed: int) -> bytes:
    rng = XorShift(seed)
    return bytes(min(255, (x + y) // 8 + rng.next() % 4) for y in range(height) for x in range(width))


def records(count: int) -> bytes:
    """A pointer, a counter, a constant and a squared counter per record: a table of structs."""
    return b"".join(struct.pack("<IIII", 0x400000 + i * 24, i * 3, 7, (i * i) & 0xFFFF) for i in range(count))


def repeats(nbytes: int, period: int, seed: int) -> bytes:
    """One period of mixed text-like bytes and runs of zeros, repeated."""
    rng = random.Random(seed)
    block = bytearray()
    while len(block) < period:
        if rng.random() < 0.3:
            block += bytes(rng.randrange(16, 512))
        else:
            block += bytes(rng.randrange(32, 127) for _ in range(rng.randrange(16, 256)))
    block = bytes(block[:period])
    return (block * (nbytes // period + 1))[:nbytes]


def random_bytes(nbytes: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(nbytes)


EDGE_CLASSES = {
    "edge-pcm8-mono": ("pcm8-mono.raw", lambda: pcm(MIB, 8, 1, 0x6D380001)),
    "edge-pcm8-stereo": ("pcm8-stereo.raw", lambda: pcm(MIB, 8, 2, 0x73380001)),
    "edge-pcm16-mono": ("pcm16-mono.raw", lambda: pcm(2 * MIB, 16, 1, 0x6D310001)),
    "edge-pcm16-stereo": ("pcm16-stereo.raw", lambda: pcm(2 * MIB, 16, 2, 0x61700001)),
    "edge-rgb24": ("rgb24.raw", lambda: rgb24(1024, 1024, 0x72670001)),
    "edge-grey": ("grey.raw", lambda: grey(1024, 1024, 0x67720001)),
    "edge-records": ("table16.raw", lambda: records(131072)),
    "edge-repeats": ("repeats.bin", lambda: repeats(4 * MIB, 8 * 1024 + 7, 551)),
    "edge-random": ("random.bin", lambda: random_bytes(MIB, 617)),
}


def seed_edge(corpora_root: Path, only: list[str] | None = None) -> dict[str, str]:
    """Write every edge class under corpora_root; returns class -> sha256 of its file."""
    out = {}
    for cls, (name, build) in EDGE_CLASSES.items():
        if only and cls not in only:
            continue
        data = build()
        target = corpora_root / cls
        target.mkdir(parents=True, exist_ok=True)
        for stale in target.iterdir():
            if stale.name != name:
                stale.unlink()
        path = target / name
        if not path.is_file() or path.read_bytes() != data:
            path.write_bytes(data)
        out[cls] = hashlib.sha256(data).hexdigest()
    return out
