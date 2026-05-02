#!/usr/bin/env bash
# Comparison 3: F5-TTS synthesis  vs  the real Majel show audio.
# For each pair, plays the actual training-set clip first, then asks the
# F5 model to synthesize the SAME transcript and plays that back-to-back.
# This is the toughest test — F5 has to convince you it could replace the
# real recording for that exact line.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/venv/bin/python"
RUN="${1:-majel_clean_run2}"
CKPT_NAME="${2:-model_600}"

CKPT="$ROOT/venv/lib/python3.10/ckpts/$RUN/$CKPT_NAME.pt"
VOCAB="$ROOT/venv/lib/python3.10/data/${RUN}_pinyin/vocab.txt"
REF_WAV="$ROOT/dataset/clips_curated/001__0.985__2.1s__Accessing_Library_Computer_Data.wav"
REF_TEXT="Accessing Library Computer Data"
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Pick six varied real clips from the curated set as ground truth.
REAL_CLIPS=(
    "001__0.985__2.1s__Accessing_Library_Computer_Data.wav"
    "003__0.667__2.8s__You_are_not_authorized_to_access_this_fa.wav"
    "027__0.467__5.3s__Selfdestruct_sequence_has_been_initiated.wav"
    "059__0.417__2.2s__Automatic_defense_procedures_initiated.wav"
    "082__0.390__2.3s__Warp_core_collapse_in_10_seconds.wav"
    "132__0.360__1.8s__Autodestruct_sequence_armed.wav"
)

echo
echo "════════════════════════════════════════════════════════════════"
echo "  COMPARISON 3: REAL Majel show audio  vs  F5-TTS (${RUN}/${CKPT_NAME})"
echo "════════════════════════════════════════════════════════════════"

i=0
for clip in "${REAL_CLIPS[@]}"; do
    i=$((i+1))
    real_wav="$ROOT/dataset/clips_curated/$clip"
    if [ ! -f "$real_wav" ]; then
        echo "── pair $i: skip (missing $clip)"
        continue
    fi
    # Pull the override transcript (full whisper retranscription).
    text="$("$PY" -c "
import json, sys
d = json.load(open('$ROOT/dataset/transcript_overrides.json'))
print(d.get('$clip', '').strip())
")"
    if [ -z "$text" ]; then
        echo "── pair $i: skip (no transcript for $clip)"
        continue
    fi
    echo
    echo "── pair $i: \"$text\""
    echo "   ▶ REAL"
    paplay "$real_wav"
    sleep 0.5

    # Synthesize the same line via F5.
    out_dir="$TMPDIR/$i"
    mkdir -p "$out_dir"
    echo "   (synthesizing F5…)"
    "$ROOT/venv/bin/f5-tts_infer-cli" \
        -m F5TTS_v1_Base \
        -p "$CKPT" \
        -v "$VOCAB" \
        -r "$REF_WAV" \
        -s "$REF_TEXT" \
        -t "$text" \
        -o "$out_dir" \
        -w "out.wav" \
        --remove_silence >/dev/null 2>&1 \
        || { echo "   (F5 synth failed)"; continue; }
    echo "   ▶ F5"
    paplay "$out_dir/out.wav"
    sleep 0.8
done
