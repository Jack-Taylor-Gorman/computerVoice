#!/usr/bin/env bash
# UserPromptSubmit hook: fires when the user submits a prompt to Claude Code.
# Play beep 4 as confirmation that the prompt was accepted.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cat >/dev/null
paplay "$DIR/sounds/computer/computerbeep_4.wav" >/dev/null 2>&1 &
disown
exit 0
