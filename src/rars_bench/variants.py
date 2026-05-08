"""Variant — encode-side option set that defines one cell of the sweep matrix.

A Variant maps onto the schema's variant key (format, method, filter, solid,
encrypted, dict_log) plus extras (password, volume_size) that the encoder
cares about but triage joins ignore.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Variant:
    format: str                 # 'rar14' for this slice
    method: str                 # 'stored' | 'compressed'
    method_int: int             # schema.method (0..5); 0 stored, 3 default compressed
    filter: str                 # 'none' for rar14 (no filter support)
    solid: bool
    encrypted: bool
    password: str | None
    dict_log: int | None        # rar14 has fixed ~64 KiB dict; leave None
    volume_size: int | None     # bytes; None = single volume

    @property
    def key(self) -> str:
        """Stable short identifier used in work-dir names and triage output."""
        bits = [self.format, self.method]
        if self.solid:
            bits.append("solid")
        if self.encrypted:
            bits.append("enc")
        if self.volume_size is not None:
            bits.append(f"v{self.volume_size}")
        return "-".join(bits)


RAR14_SLICE: list[Variant] = [
    Variant(
        format="rar14",
        method="compressed",
        method_int=3,
        filter="none",
        solid=False,
        encrypted=False,
        password=None,
        dict_log=None,
        volume_size=None,
    ),
]
# TODO follow-up variants once the slice is green:
#   stored, compressed+solid, compressed+password, compressed+volumes
