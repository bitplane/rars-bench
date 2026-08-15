"""RAR 2.0 variant set. Oracle: RAR 2.50 DOS binary under DOSBox-X.

RAR 2.50 emits the same wire format as RAR 2.0 (UNP_VER=20). It's the
earliest oracle that supports per-level method-byte stamping (rar1.402's
writer has no level concept). Default dict on the wire is 64K.
"""
from ._common import baseline_variants


RAR20_DEFAULT = baseline_variants("rar20")
