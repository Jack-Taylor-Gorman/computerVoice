#!/usr/bin/env bash
# Audition all checkpoints from a finetune run on the eval prompt deck.
#
# Output: dataset/audition/<run_name>/<ckpt>/<prompt_idx>.wav
# Run names like: majel_clean_run2.
#
# Usage:  scripts/audition_f5.sh majel_clean_run2 [ref_clip_id]
set -euo pipefail

RUN="${1:?usage: $0 <run_name> [ref_clip_id]}"
REF_CLIP="${2:-001__0.985__2.1s__Accessing_Library_Computer_Data}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/venv/bin/python"
CKPT_DIR="$ROOT/venv/lib/python3.10/ckpts/$RUN"
VOCAB="$ROOT/venv/lib/python3.10/data/${RUN}_pinyin/vocab.txt"
REF_WAV="$ROOT/dataset/clips_curated/${REF_CLIP}.wav"
OUT_BASE="$ROOT/dataset/audition/$RUN"

if [ ! -d "$CKPT_DIR" ]; then
    echo "missing: $CKPT_DIR" >&2; exit 2
fi
if [ ! -f "$REF_WAV" ]; then
    echo "missing reference wav: $REF_WAV" >&2
    echo "available:"; ls "$ROOT/dataset/clips_curated/" | head; exit 2
fi

# Pull ref text from the manifest entry for the chosen clip.
REF_TEXT="$("$PY" -c "
import json, sys
for line in open('$ROOT/dataset/manifest_curated.jsonl'):
    r = json.loads(line)
    if r.get('clip_id', '').startswith('$REF_CLIP'):
        print(r['transcript']); sys.exit(0)
print('Accessing library computer data.')
")"

echo "ref clip:  $REF_CLIP"
echo "ref text:  $REF_TEXT"
echo "ckpts:     $(ls -1 $CKPT_DIR/*.pt | wc -l)"
echo

PROMPTS=(
    "Captain on the bridge."
    "Warp core breach in three minutes."
    "Access denied. Restricted materials."
    "Diagnostic complete. All systems nominal."
    "Working."
    "Hello, world. This is the Majel computer voice."
)

for ckpt in $(ls "$CKPT_DIR"/*.pt | sort); do
    name="$(basename "$ckpt" .pt)"
    out_dir="$OUT_BASE/$name"
    mkdir -p "$out_dir"
    echo "=== $name ==="
    for i in "${!PROMPTS[@]}"; do
        prompt="${PROMPTS[$i]}"
        out_wav="$out_dir/$(printf '%02d' $((i+1))).wav"
        if [ -f "$out_wav" ]; then
            echo "  [skip] $(basename "$out_wav") already exists"; continue
        fi
        echo "  [gen ] prompt $((i+1))/${#PROMPTS[@]}: ${prompt:0:50}"
        "$ROOT/venv/bin/f5-tts_infer-cli" \
            -m F5TTS_v1_Base \
            -p "$ckpt" \
            -v "$VOCAB" \
            -r "$REF_WAV" \
            -s "$REF_TEXT" \
            -t "$prompt" \
            -o "$out_dir" \
            -w "$(printf '%02d' $((i+1))).wav" \
            --remove_silence \
            >> "$out_dir/_infer.log" 2>&1 || echo "    (failed — see $out_dir/_infer.log)"
    done
done

echo
echo "=== AUDITION READY ==="
echo "Listen with:  paplay $OUT_BASE/<ckpt>/<prompt>.wav"
echo "Or open the folder: nautilus $OUT_BASE"
