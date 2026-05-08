"""RAR 2.9 variant set.

Oracle is WinRAR 2.90 under wine. wrar290 does NOT expose PPMd via the
CLI (no -mc switch), so the cross-encoder variant axis only covers LZ:
{stored, lz-1..lz-5}. PPMd cross-compatibility is captured in a separate
one-shot probe (rars writes PPMd, wrar290 attempts to read).

Followups (out of slice):
  - rars-only PPMd variant for self-roundtrip baseline
  - encrypted variants (rar 2.9 supports passwords)
  - solid variant (`-s` on wrar290, `--solid` on rars)
  - multi-volume
"""
from ._common import baseline_variants


RAR29_DEFAULT = baseline_variants("rar29")
