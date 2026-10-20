#!/bin/bash
# Render each piece, build its subtitled MP4, open it. Sequential.
#
# Usage:
#   scripts/render_pieces_pipeline.sh <pieces_dir> [START_PIECE]
#
# Resumable: skips pieces whose complete_subtitled.mp4 already exists.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PIECES_DIR="${1:?need pieces dir}"
START="${2:-1}"
PY="$ROOT/venv/bin/python"

shopt -s nullglob
PIECE_FILES=( "$PIECES_DIR"/piece_*.cleaned.txt )
TOTAL=${#PIECE_FILES[@]}
echo "pieces: $TOTAL  starting at piece $START"

for SRC in "${PIECE_FILES[@]}"; do
    STEM=$(basename "$SRC" .cleaned.txt)        # piece_NN
    N=${STEM#piece_}; N=${N#0}; [ -z "$N" ] && N=0
    if [ "$N" -lt "$START" ]; then continue; fi
    OUT="$PIECES_DIR/${STEM}_out"
    MP4="$OUT/complete_subtitled.mp4"
    if [ -f "$MP4" ]; then
        echo "[$STEM] already has video, opening"
        xdg-open "$MP4" >/dev/null 2>&1 &
        sleep 2
        continue
    fi
    echo "============================================================"
    echo "[$STEM] rendering with QC retry loop"
    echo "============================================================"
    mkdir -p "$OUT/chunks" "$OUT/raw"
    "$ROOT/scripts/render_with_qc.sh" \
        "$SRC" \
        "$PIECES_DIR/${STEM}.attribution.jsonl" \
        "$OUT" \
        "$PIECES_DIR/${STEM}.pacing.jsonl" \
        3
    echo "[$STEM] building subtitled video"
    WHISPER_MODEL=medium "$PY" scripts/make_subtitle_video.py "$OUT/complete.wav"
    echo "[$STEM] opening"
    xdg-open "$MP4" >/dev/null 2>&1 &
done

echo "all pieces done"
