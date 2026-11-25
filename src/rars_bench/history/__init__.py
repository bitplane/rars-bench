"""Per-version results kept in the repo, so every rars release can be compared with the last.

Layout: `results/<rars version or tag>/<machine-id>-<tier>.json`. Each file
holds the machine's profile, the tools with their versions and hashes, and
one entry per cell with its raw metrics and its ratios to the reference tool
from the same run. Ratios are what carry across machines; raw times and
memory only compare on the same machine.
"""
