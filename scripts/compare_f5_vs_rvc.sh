#!/usr/bin/env bash
# Comparison 2: F5-TTS finetune  vs  production RVC (edge-tts→Majel.pth).
# Synthesizes each prompt through speak.py (which is the prod stack)
# and plays it back-to-back against the pre-generated F5 audition wav.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN="${1:-majel_clean_run2}"
CKPT="${2:-model_600}"

PROMPTS=(
    "Captain on the bridge."
    "Warp core breach in three minutes."
    "Access denied. Restricted materials."
    "Diagnostic complete. All systems nominal."
    "Working."
    "Hello, world. This is the Majel computer voice."
)

echo
echo "════════════════════════════════════════════════════════════════"
echo "  COMPARISON 2: F5-TTS (${RUN}/${CKPT})  vs  RVC (production)"
echo "════════════════════════════════════════════════════════════════"

for i in "${!PROMPTS[@]}"; do
    idx="$(printf '%02d' $((i+1)))"
    prompt="${PROMPTS[$i]}"
    f5_wav="$ROOT/dataset/audition/$RUN/$CKPT/$idx.wav"
    echo
    echo "── prompt $((i+1)): $prompt"
    if [ -f "$f5_wav" ]; then
        echo "   ▶ F5-TTS"
        paplay "$f5_wav"
        sleep 0.5
    else
        echo "   (f5 wav missing: $f5_wav)"
    fi
    echo "   ▶ RVC (live synth via speak.py — ~5–10s)"
    # speak.py blocks on its own paplay so this naturally serializes.
    # Disable the pre-voice beep + duck/restore to keep the comparison clean.
    MAJEL_LOG=/tmp/majel_compare.log printf '%s' "$prompt" \
        | "$ROOT/venv/bin/python" "$ROOT/speak.py" >> /tmp/majel_compare.log 2>&1 \
        || echo "   (RVC synth failed; check /tmp/majel_compare.log)"
    sleep 0.8
done
