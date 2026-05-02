#!/usr/bin/env bash
# Comparison 1: clean (78 clips) vs all (103 clips, incl. flagged).
# Same prompt, same checkpoint number, played back-to-back.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CKPT="${1:-model_600}"

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
echo "  COMPARISON 1: CLEAN (78 clips)  vs  ALL (103 clips, +flagged)"
echo "  checkpoint: $CKPT"
echo "════════════════════════════════════════════════════════════════"

for i in "${!PROMPTS[@]}"; do
    idx="$(printf '%02d' $((i+1)))"
    prompt="${PROMPTS[$i]}"
    clean_wav="$ROOT/dataset/audition/majel_clean_run2/$CKPT/$idx.wav"
    all_wav="$ROOT/dataset/audition/majel_all_run2/$CKPT/$idx.wav"
    echo
    echo "── prompt $((i+1)): $prompt"
    if [ -f "$clean_wav" ]; then
        echo "   ▶ CLEAN"
        paplay "$clean_wav"
        sleep 0.5
    else
        echo "   (clean wav missing: $clean_wav)"
    fi
    if [ -f "$all_wav" ]; then
        echo "   ▶ ALL"
        paplay "$all_wav"
        sleep 0.8
    else
        echo "   (all wav missing: $all_wav)"
    fi
done
