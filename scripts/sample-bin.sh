#!/usr/bin/env bash
# Sample N random executables from /usr/bin into corpora/binary-x86_64/.
# Usage: sample-bin.sh [N=50]
set -euo pipefail

N="${1:-50}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HERE/../corpora/binary-x86_64"

mkdir -p "$DEST"
find /usr/bin -maxdepth 1 -type f -executable -size +1k -size -10M 2>/dev/null \
    | shuf \
    | head -n "$N" \
    | while IFS= read -r f; do
        cp -- "$f" "$DEST/"
    done

count=$(find "$DEST" -maxdepth 1 -type f | wc -l)
echo "sampled into $DEST (now $count files)"
