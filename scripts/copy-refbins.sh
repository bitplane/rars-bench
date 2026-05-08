#!/usr/bin/env bash
# Mirror reference RAR binaries + wineprefixes into ./_refs/ from ../rar.
#
# Note: ../rar/_refs/rarbins/RAR1_402.EXE is a different (installer) variant
# than ../rar/fixtures/1.402/.rar1402-bin/RAR.EXE — only the latter is the
# real DOS executable that runs under DOSBox-X. We prefer the fixtures path.
#
# wrar290.exe is a self-extracting installer; we extract Rar.exe into a
# wineprefix-style layout so WineRarDriver can launch it directly without
# running the installer GUI.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST_BINS="$HERE/../_refs/rarbins"
DEST_PREFIX="$HERE/../_refs/wineprefixes"
RAR_REPO="${RAR_REPO:-/home/gaz/src/tmp/rar}"
mkdir -p "$DEST_BINS" "$DEST_PREFIX"

# 1. RAR 1.402 (DOSBox oracle)
RAR1402_FIXTURE="$RAR_REPO/fixtures/1.402/.rar1402-bin/RAR.EXE"
RAR1402_REFBINS="$RAR_REPO/_refs/rarbins/RAR1_402.EXE"
if [ -f "$RAR1402_FIXTURE" ]; then
    cp "$RAR1402_FIXTURE" "$DEST_BINS/RAR1_402.EXE"
    echo "mirrored RAR1_402.EXE (fixtures, $(stat -c %s "$RAR1402_FIXTURE") bytes)"
elif [ -f "$RAR1402_REFBINS" ]; then
    cp "$RAR1402_REFBINS" "$DEST_BINS/RAR1_402.EXE"
    echo "warning: using non-validated $RAR1402_REFBINS — DOSBox runs may fail" >&2
else
    echo "error: no RAR1_402.EXE found in $RAR_REPO" >&2
    exit 1
fi

# 2. WinRAR 2.90 wineprefix (rar29 oracle)
SRC_290_INSTALLER="$RAR_REPO/_refs/rarbins/wrar290.exe"
SRC_290_PREFIX="$RAR_REPO/_refs/wineprefixes/winrar290"
DEST_290_PREFIX="$DEST_PREFIX/winrar290"
DEST_290_RAR_DIR="$DEST_290_PREFIX/drive_c/Program Files (x86)/WinRAR"
if [ -f "$SRC_290_PREFIX/drive_c/Program Files (x86)/WinRAR/Rar.exe" ]; then
    # Already-populated upstream prefix: mirror it whole (drive_c + .reg).
    rm -rf "$DEST_290_PREFIX"
    mkdir -p "$DEST_290_PREFIX"
    # cp -a preserves symlinks (the dosdevices entries c:, z: etc are
    # symlinks; cp -r would dereference them and break the prefix).
    cp -a "$SRC_290_PREFIX/." "$DEST_290_PREFIX/"
    echo "mirrored winrar290 prefix (whole wineprefix including .reg files)"
elif [ -f "$SRC_290_INSTALLER" ]; then
    # Installer present but no extracted prefix upstream — extract it via
    # any available RAR.
    EXTRACTOR=""
    for cand in \
        "$RAR_REPO/_refs/rarbins/rar393-linux-x64" \
        "$RAR_REPO/_refs/rarbins/rar712-linux-x64" \
        "$(command -v rar || true)" \
        "$(command -v unrar || true)"
    do
        if [ -n "$cand" ] && [ -x "$cand" ]; then
            EXTRACTOR="$cand"
            break
        fi
    done
    if [ -z "$EXTRACTOR" ]; then
        echo "error: no RAR/unrar binary available to extract wrar290.exe" >&2
        exit 1
    fi
    TMP=$(mktemp -d)
    (cd "$TMP" && "$EXTRACTOR" x -y "$SRC_290_INSTALLER" >/dev/null)
    mkdir -p "$DEST_290_RAR_DIR"
    cp -r "$TMP/." "$DEST_290_RAR_DIR/"
    rm -rf "$TMP"
    echo "extracted wrar290.exe with $EXTRACTOR -> $DEST_290_RAR_DIR"
else
    echo "warning: neither $SRC_290_PREFIX nor $SRC_290_INSTALLER present; rar29 oracle unavailable" >&2
fi

# 3. Mirror additional wineprefixes (already-populated upstream).
mirror_prefix() {
    local name="$1"
    local src="$RAR_REPO/_refs/wineprefixes/$name"
    local dest="$DEST_PREFIX/$name"
    if [ ! -d "$src" ]; then
        echo "skipping $name (no upstream prefix at $src)"
        return 0
    fi
    rm -rf "$dest"
    mkdir -p "$dest"
    cp -a "$src/." "$dest/"
    echo "mirrored $name"
}
mirror_prefix winrar300
mirror_prefix winrar420
mirror_prefix winrar602
mirror_prefix winrar721

[ -f "$RAR_REPO/_refs/rarbins/SHA256SUMS.txt" ] && cp "$RAR_REPO/_refs/rarbins/SHA256SUMS.txt" "$DEST_BINS/" || true
