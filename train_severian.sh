#!/usr/bin/env bash
# One-shot Severian F5-TTS finetune pipeline.
#
# Steps:
#   1. (skipped if cache present) WhisperX-transcribe + segment the audiobook
#      into 5–15s sentence-aligned clips.
#   2. Build an F5-TTS bundle in dataset_avers/datasets/avers_run1/.
#   3. Run scripts/finetune_f5.sh on that bundle. F5 prepare + finetune-cli
#      land the checkpoint in venv/lib/python3.10/ckpts/avers_run1/.
#
# After this finishes, restart the F5 daemon (it will load the new ckpt by
# default — see f5_daemon.py:RUN env var) and the next utterance speaks in
# Severian's voice.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/venv/bin/python"

INPUT="${SEVERIAN_INPUT:-$DIR/dataset_avers/source/shadow_of_the_torturer.wav}"
NAME="${SEVERIAN_RUN_NAME:-avers_run1}"
MAX_CLIPS="${SEVERIAN_MAX_CLIPS:-}"  # leave empty to use all clips
WHISPER_MODEL="${WHISPER_MODEL:-medium}"  # large-v3 OOMs on 8GB GPUs

if [ ! -f "$INPUT" ]; then
    echo "input audiobook missing: $INPUT" >&2
    echo "fix with: yt-dlp -f bestaudio --extract-audio --audio-format wav -o $INPUT '<youtube-url>'" >&2
    exit 2
fi

echo "[$(date +%H:%M:%S)] === Severian training pipeline ==="
echo "  input:  $INPUT"
echo "  bundle: dataset_avers/datasets/$NAME"
echo "  whisper model: $WHISPER_MODEL"

# Step 1 — mine. Idempotent: cached ASR is reused if cache/asr.json exists.
echo "[$(date +%H:%M:%S)] step 1/3 — mining clips"
WHISPER_MODEL="$WHISPER_MODEL" "$PY" "$DIR/scripts/mine_audiobook.py" \
    --input "$INPUT" \
    --out "$DIR/dataset_avers" \
    --device "${SEVERIAN_DEVICE:-cuda:0}"

# Step 2 — bundle.
echo "[$(date +%H:%M:%S)] step 2/3 — building F5 bundle"
BUILD_ARGS=(--name "$NAME")
if [ -n "$MAX_CLIPS" ]; then
    BUILD_ARGS+=(--max-clips "$MAX_CLIPS")
fi
"$PY" "$DIR/scripts/build_avers_bundle.py" "${BUILD_ARGS[@]}"

# Step 3 — finetune. Reuses the existing F5 finetune launcher.
echo "[$(date +%H:%M:%S)] step 3/3 — F5 finetune"
exec "$DIR/scripts/finetune_f5.sh" "$DIR/dataset_avers/datasets/$NAME"
