#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HERE/../corpora/text-small"
mkdir -p "$DEST"

: > "$DEST/empty.bin"
printf 'A' > "$DEST/single.bin"
dd if=/dev/zero of="$DEST/zeros.bin" bs=4096 count=1 status=none
python3 -c 'import sys; sys.stdout.buffer.write((b"abc" * 342)[:1024])' > "$DEST/repeats.bin"
cat > "$DEST/mixed.txt" <<'EOF'
The quick brown fox jumps over the lazy dog.
A wizard's job is to vex chumps quickly in fog.
Pack my box with five dozen liquor jugs.
How vexingly quick daft zebras jump!
Sphinx of black quartz, judge my vow.
The five boxing wizards jump quickly.
Crazy Fredrick bought many very exquisite opal jewels.
We promptly judged antique ivory buckles for the prize.
Heavy boxes perform quick waltzes and jigs.
Jaded zombies acted quaintly but kept driving their oxen forward.
Two driven jocks help fax my big quiz.
The job requires extra pluck and zeal from every young wage earner.
A quart jar of oil mixed with zinc oxide makes a very bright paint.
Public junk yards muffle noise of crashing vehicles every day.
Sixty zippers were quickly picked from the woven jute bag.
Watch Jeopardy! Alex Trebek's fun TV quiz game show.
Five quacking zephyrs jolt my wax bed.
Foxy diva Jennifer Lopez wasn't baking my quiche.
EOF

echo "seeded $DEST ($(find "$DEST" -maxdepth 1 -type f | wc -l) files)"
