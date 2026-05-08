"""Shared variant builders for the rar15-rar70 family.

All these formats share the same six-cell baseline: stored (-m0) plus
lz-1..lz-5 (-m1..-m5). Per-format extras (PPMd flags, larger dict sizes,
RAR5/7 specifics) belong in follow-up variant sets.
"""
from ..variants import Variant


def baseline_variants(fmt: str) -> list[Variant]:
    cells = [
        Variant(
            format=fmt, method="stored", method_int=0, filter="none",
            solid=False, encrypted=False, password=None,
            dict_log=None, volume_size=None,
        ),
    ]
    for level in range(1, 6):
        cells.append(
            Variant(
                format=fmt, method=f"lz-{level}", method_int=level,
                filter="none", solid=False, encrypted=False, password=None,
                dict_log=None, volume_size=None,
            )
        )
    return cells
