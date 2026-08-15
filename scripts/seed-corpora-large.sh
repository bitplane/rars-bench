#!/usr/bin/env bash
# Seed corpora/large-* with members big enough to exercise a modern RAR writer.
#
# The small tier (seed-corpora.sh) caps files at 64 KiB, because it was built
# for the rar14 sweep and RAR 1.4's dictionary is 64 KiB. That cap was right
# for that sweep and wrong for everything since: nothing in it reaches past
# 128 KiB, so a RAR 5 writer's dictionary sizing, block sizing and filter
# region screening are all invisible to it, and the ratio gap it reports
# against WinRAR (+3%) is a third of what real files show (+11% to +30%).
#
# So: same harness, second tier. Sweep the small tier for rar14 and rar20,
# both tiers for anything newer.
#
# Sizes are deliberately modest. A single 4 MB member already crosses every
# threshold that matters; the point is coverage of the size axis, not bulk,
# and a sweep that takes an hour is a sweep nobody runs.
#
# Everything is sourced from local files or generated deterministically, so
# reseeding produces identical bytes and the oracle encode cache stays valid.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HERE/../corpora"

TARGET_BYTES=$((4 * 1024 * 1024))

# Where to look for a source tree for the large-source-tree class. Override
# when the rars checkout is not a sibling of this repo, which it usually is not.
RARS_REPO="${RARS_REPO:-}"

fresh() {
    local target="$DEST/$1"
    rm -rf "$target"
    mkdir -p "$target"
    echo "$target"
}

report() {
    local name="$1"
    local dir="$DEST/$name"
    local files bytes
    files=$(find "$dir" -type f | wc -l)
    bytes=$(find "$dir" -type f -exec stat -c %s {} + 2>/dev/null | awk '{s+=$1} END {print s+0}')
    printf "  %-24s %2s files  %9s bytes" "$name" "$files" "$bytes"
    # An empty class produces no rows rather than an error, so a sweep would
    # quietly cover less than it claims. Say so here where someone will see it.
    if [ "$files" -eq 0 ]; then
        printf "   EMPTY: no source files matched on this machine"
        EMPTY_CLASSES=$((EMPTY_CLASSES + 1))
    fi
    printf "\n"
}

EMPTY_CLASSES=0

# Largest-first so the pick is stable across machines with different file sets,
# and so we land near the target rather than dribbling up to it.
pick_by_size() {
    local dir="$1" want="$2"; shift 2
    local total=0
    for src in "$@"; do
        [ -r "$src" ] && [ -f "$src" ] || continue
        local size
        size=$(stat -Lc %s "$src") || continue
        [ $((total + size)) -gt "$want" ] && continue
        cp -L "$src" "$dir/$(basename "$src")" 2>/dev/null || continue
        total=$((total + size))
    done
}

echo "seeding large corpus tier:"

# 1. Stripped x86-64 objects. The ordinary case: real code, no debug data,
#    one big region the x86 filter should cover end to end.
dir=$(fresh "large-bin-stripped")
mapfile -t stripped < <(
    for f in $(find /usr/lib/x86_64-linux-gnu /usr/bin -maxdepth 1 -type f -size +1M -size -6M 2>/dev/null); do
        file -b "$f" 2>/dev/null | grep -q "ELF.*, stripped" && echo "$(stat -Lc%s "$f") $f"
    done | sort -rn | cut -d' ' -f2-
)
pick_by_size "$dir" "$TARGET_BYTES" "${stripped[@]}"
report "large-bin-stripped"

# 2. Unstripped objects. Their own class because the debug sections are most
#    of the file and are not code, which is exactly the shape that made the
#    filter search sample DWARF and decline a filter that was worth having.
dir=$(fresh "large-bin-unstripped")
mapfile -t unstripped < <(
    for f in $(find /usr/lib/x86_64-linux-gnu /usr/bin -maxdepth 1 -type f -size +1M -size -6M 2>/dev/null); do
        file -b "$f" 2>/dev/null | grep -q "not stripped" && echo "$(stat -Lc%s "$f") $f"
    done | sort -rn | cut -d' ' -f2-
)
pick_by_size "$dir" "$TARGET_BYTES" "${unstripped[@]}"
report "large-bin-unstripped"

# 3. Text at a size where the dictionary decides the outcome. On 6 MB of
#    prose the writer's 1 MiB cap costs about 19 percentage points against
#    WinRAR's 32 MB default, and no file in the small tier can show that.
dir=$(fresh "large-text")
out="$dir/manpages.txt"
: > "$out"
while read -r gz; do
    [ "$(stat -c%s "$out")" -ge "$TARGET_BYTES" ] && break
    zcat "$gz" 2>/dev/null >> "$out" || true
done < <(find /usr/share/man -name '*.gz' -readable 2>/dev/null | sort)
truncate -s "$TARGET_BYTES" "$out"
report "large-text"

# 4. A source tree, which is what people actually put in archives: many small
#    text files with heavy cross-file repetition, presented as one member.
#
#    Built from a file list rather than tarring a directory, because a whole
#    tree is whatever size it happens to be. The first attempt fell through a
#    stale ../rars path to /usr/include and produced a 144 MB member.
dir=$(fresh "large-source-tree")
src_root=""
for candidate in "$RARS_REPO" "$HERE/../../rars" "$HOME/src/rars" /usr/include; do
    [ -n "$candidate" ] && [ -d "$candidate" ] && { src_root="$candidate"; break; }
done
if [ -n "$src_root" ]; then
    list=$(mktemp)
    total=0
    while read -r rel; do
        size=$(stat -Lc%s "$src_root/$rel" 2>/dev/null) || continue
        [ $((total + size)) -gt "$TARGET_BYTES" ] && continue
        printf '%s\n' "$rel" >> "$list"
        total=$((total + size))
    done < <(cd "$src_root" && find . -type f \
        \( -name '*.rs' -o -name '*.py' -o -name '*.h' -o -name '*.c' -o -name '*.md' -o -name '*.toml' \) \
        -not -path './target/*' -not -path './.git/*' 2>/dev/null | sort)
    tar -C "$src_root" -cf "$dir/source-tree.tar" -T "$list" 2>/dev/null || true
    rm -f "$list"
fi
report "large-source-tree"

# 5. Already compressed. Nothing should help, and the store fallback and the
#    filter screens both have to notice that without paying to find out.
dir=$(fresh "large-incompressible")
mapfile -t packed < <(
    find /usr/lib/firmware /usr/share -maxdepth 4 \
        \( -name '*.zst' -o -name '*.xz' -o -name '*.gz' \) -size +100k 2>/dev/null | sort
)
pick_by_size "$dir" "$TARGET_BYTES" "${packed[@]}"
report "large-incompressible"

# 6. The degenerate end. Deterministic rather than /dev/urandom so reseeding
#    does not invalidate the oracle encode cache.
dir=$(fresh "large-compressible")
python3 - "$dir/repetitive.bin" "$TARGET_BYTES" <<'PY'
import sys

path, total = sys.argv[1], int(sys.argv[2])
# A few long runs, then a phrase repeating at an offset no window aligns to,
# so it rewards long matches without being a single trivial run.
block = b"\x00" * 4096 + b"the quick brown fox jumps over the lazy dog. " * 91
with open(path, "wb") as f:
    written = 0
    while written < total:
        chunk = block[: total - written]
        f.write(chunk)
        written += len(chunk)
PY
report "large-compressible"

echo
echo "large tier total: $(find "$DEST" -path '*/large-*' -type f -exec stat -c %s {} + | awk '{s+=$1} END {print s+0}') bytes"
if [ "$EMPTY_CLASSES" -gt 0 ]; then
    echo "warning: $EMPTY_CLASSES class(es) came out empty; a sweep will silently skip them" >&2
fi
