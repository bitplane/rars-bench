"""RAR 5.0 variant set. Oracle: WinRAR 6.02 under wine.

RAR 5.0 has different method semantics from 3.x/4.x but the bench's
high-level keys (stored / lz-1..lz-5) still map cleanly to `-m0..-m5`.
The wire format is fundamentally different — different
block layout, BLAKE2sp instead of CRC32 for some integrity, but our
cross-test against `rar t` doesn't care about that.
"""
from ._common import baseline_variants


RAR50_DEFAULT = baseline_variants("rar50")
