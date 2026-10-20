#!/usr/bin/env bash
# UserPromptSubmit hook: fires when the user submits a prompt to Claude Code.
# Play beep 4 as confirmation that the prompt was accepted.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cat >/dev/null
paplay "$DIR/sounds/computer/computerbeep_4.wav" >/dev/null 2>&1 &
disown
# The Stop hook will speak the reply, and the voice model unloads after ten
# idle minutes (f5_daemon.py). Ask the front to load it now, while Claude is
# still working, so the reply isn't delayed by a ~3 s cold start. Fire and
# forget: the front finishes loading even if this client has gone.
python3 -c '
import socket
s = socket.socket(socket.AF_UNIX)
s.settimeout(1)
s.connect("/tmp/majel_f5.sock")
s.sendall(b"{\"warm\": true}\n")
' >/dev/null 2>&1 &
disown
exit 0
