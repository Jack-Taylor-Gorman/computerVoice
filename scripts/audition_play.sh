#!/usr/bin/env bash
# Walk through every audition prompt and play it across all 7 checkpoints
# sequentially. Lets you A/B by ear which checkpoint sounds most like Majel.
#
# Usage:  scripts/audition_play.sh majel_clean_run2 [prompt_idx]
#         (omit prompt_idx to play all 6 prompts in turn)
set -euo pipefail

RUN="${1:?usage: $0 <run_name> [prompt_idx]}"
ONLY="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/dataset/audition/$RUN"

PROMPTS=(
    "Captain on the bridge."
    "Warp core breach in three minutes."
    "Access denied. Restricted materials."
    "Diagnostic complete. All systems nominal."
    "Working."
    "Hello, world. This is the Majel computer voice."
)

ckpts=($(ls "$DIR" | grep -E "^model_" | sort -V))
if [ ${#ckpts[@]} -eq 0 ]; then
    echo "no checkpoints in $DIR" >&2; exit 2
fi

play_prompt() {
    local i="$1"
    local idx="$(printf '%02d' "$i")"
    echo
    echo "=== prompt $i: ${PROMPTS[$((i-1))]} ==="
    for ck in "${ckpts[@]}"; do
        wav="$DIR/$ck/$idx.wav"
        if [ ! -f "$wav" ]; then echo "  (missing $ck/$idx.wav)"; continue; fi
        echo "  → $ck"
        paplay "$wav"
        sleep 0.4
    done
}

if [ -n "$ONLY" ]; then
    play_prompt "$ONLY"
else
    for i in 1 2 3 4 5 6; do play_prompt "$i"; done
fi
