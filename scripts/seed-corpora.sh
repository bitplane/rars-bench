#!/usr/bin/env bash
# Seed corpora/ with 10 themed dirs of real system files.
#
# Each dir caps at ~256 KiB total / ~64 KiB per file (RAR 1.4 dict size is
# 64 KiB so larger files won't add compression headroom for the rar14 sweep).
# This is throwaway corpus — all sources are local, nothing leaves the box.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$HERE/.."
DEST="$ROOT/corpora"

PER_FILE_MAX=$((64 * 1024))      # 64 KiB
PER_DIR_MAX=$((256 * 1024))      # 256 KiB

# Copy a list of source paths into a corpus subdir, respecting per-file and
# per-dir caps. Skips unreadable / over-sized files.
copy_capped() {
    local subdir="$1"; shift
    local target="$DEST/$subdir"
    mkdir -p "$target"
    rm -f "$target"/*

    local total=0
    local n=0
    for src in "$@"; do
        [ -r "$src" ] || continue
        [ -f "$src" ] || continue
        local size
        # -L follows symlinks so we measure what `cp` will actually copy.
        size=$(stat -Lc %s "$src" 2>/dev/null) || continue
        if [ "$size" -gt "$PER_FILE_MAX" ]; then
            continue
        fi
        if [ $((total + size)) -gt "$PER_DIR_MAX" ]; then
            continue
        fi
        # Dest name: include parent dir to avoid collisions when basenames
        # repeat across source paths (e.g. /usr/share/doc/*/copyright).
        local base
        base=$(basename "$src")
        local parent
        parent=$(basename "$(dirname "$src")")
        local dest="$target/$base"
        if [ -e "$dest" ]; then
            dest="$target/${parent}__${base}"
        fi
        cp -L "$src" "$dest" 2>/dev/null || continue
        total=$((total + size))
        n=$((n + 1))
        if [ "$n" -ge 20 ]; then
            break
        fi
    done
    echo "  $subdir: $n files, $total bytes"
}

mkdir -p "$DEST"

echo "seeding 10 corpus dirs:"

# 1. Logs — text, line-based, very compressible
mapfile -t logs < <(find /var/log -maxdepth 2 -type f -name '*.log' -readable -size +1k 2>/dev/null | head -30)
copy_capped "logs" "${logs[@]}"

# 2. C headers — source code, very compressible
mapfile -t headers < <(find /usr/include -maxdepth 2 -name '*.h' -size +1k -readable 2>/dev/null | head -40)
copy_capped "headers" "${headers[@]}"

# 3. Man pages (decompressed) — very compressible text
mkdir -p "$DEST/manpages"
rm -f "$DEST/manpages"/*
n=0; total=0
for gz in $(find /usr/share/man/man1 -name '*.gz' -readable 2>/dev/null | head -40); do
    base=$(basename "$gz" .gz)
    out="$DEST/manpages/$base"
    if zcat "$gz" > "$out" 2>/dev/null; then
        size=$(stat -c %s "$out")
        if [ "$size" -gt "$PER_FILE_MAX" ] || [ $((total + size)) -gt "$PER_DIR_MAX" ]; then
            rm -f "$out"
        else
            total=$((total + size))
            n=$((n + 1))
            [ "$n" -ge 15 ] && break
        fi
    fi
done
echo "  manpages: $n files, $total bytes"

# 4. Small ELF binaries — mid compressibility
mapfile -t bin_elf < <(find /usr/bin -maxdepth 1 -type f -size -32k -size +4k -executable 2>/dev/null | shuf -n 30)
copy_capped "bin-elf" "${bin_elf[@]}"

# 5. Shell scripts in /usr/bin — text, very compressible
mkdir -p "$DEST/bin-shell"
rm -f "$DEST/bin-shell"/*
n=0; total=0
for f in $(find /usr/bin -maxdepth 1 -type f -size -32k -readable 2>/dev/null | head -200); do
    if head -c 4 "$f" 2>/dev/null | grep -aq '^#!'; then
        size=$(stat -c %s "$f")
        if [ "$size" -le "$PER_FILE_MAX" ] && [ $((total + size)) -le "$PER_DIR_MAX" ]; then
            cp "$f" "$DEST/bin-shell/$(basename "$f")"
            total=$((total + size))
            n=$((n + 1))
            [ "$n" -ge 15 ] && break
        fi
    fi
done
echo "  bin-shell: $n files, $total bytes"

# 6. Copyright / doc text — very compressible
mapfile -t doc_text < <(find /usr/share/doc -maxdepth 2 -name 'copyright' -size +1k -size -32k 2>/dev/null | head -20)
copy_capped "doc-text" "${doc_text[@]}"

# 7. PNG icons — already compressed, low ratio (stress test)
mapfile -t icons < <(find /usr/share/icons -name '*.png' -size +1k -size -16k 2>/dev/null | shuf -n 30)
copy_capped "icons-png" "${icons[@]}"

# 8. TTF fonts — binary, mid
mapfile -t fonts < <(find /usr/share/fonts -name '*.ttf' -size -64k -size +4k 2>/dev/null | head -10)
copy_capped "fonts-ttf" "${fonts[@]}"

# 9. ~/Downloads mixed — heterogeneous real files
mapfile -t dls < <(find "$HOME/Downloads" -maxdepth 1 -type f -readable -size +1k -size -64k 2>/dev/null | head -30)
copy_capped "downloads" "${dls[@]}"

# 10. Mixed system — bag of varied things
mkdir -p "$DEST/mixed"
rm -f "$DEST/mixed"/*
mixed_sources=(
    /etc/services
    /etc/passwd
    /etc/hosts
    /etc/protocols
    /etc/lsb-release
    /etc/os-release
    /usr/share/dict/words
    /usr/share/common-licenses/GPL-3
    /usr/share/common-licenses/MIT
    /usr/share/common-licenses/BSD
)
copy_capped "mixed" "${mixed_sources[@]}"

echo
echo "summary:"
for d in "$DEST"/*/; do
    name=$(basename "$d")
    [ "$name" = ".gitkeep" ] && continue
    files=$(find "$d" -maxdepth 1 -type f | wc -l)
    bytes=$(find "$d" -maxdepth 1 -type f -exec stat -c %s {} + 2>/dev/null | awk '{s+=$1} END {print s+0}')
    printf "  %-15s %3s files  %8s bytes\n" "$name" "$files" "$bytes"
done
