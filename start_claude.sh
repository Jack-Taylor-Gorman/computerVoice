#!/usr/bin/env bash
# Launch Claude Code with stdout captured to /tmp/claude-stream.log so
# tty_watcher.py can detect approval menus instantly.
# Usage: ./start_claude.sh [args passed to claude]
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/tmp/claude-stream.log
# Keep ~/.local out of the import path so the venv's CUDA torch wins over a
# stale CPU torch wheel that may live in user-site.
export PYTHONNOUSERSITE=1

# Start the watcher if not already running.
if ! pgrep -f "$DIR/tty_watcher.py" >/dev/null; then
    setsid env PYTHONNOUSERSITE=1 "$DIR/venv/bin/python" "$DIR/tty_watcher.py" </dev/null \
        >/tmp/tty_watcher.log 2>&1 &
    disown
fi

: > "$LOG"

# script(1): -f flushes after each write, -q suppresses start/end banners,
# -c runs claude inside the PTY. Your terminal still shows normal output
# because script tees to both the log file and the controlling tty.
exec script -f -q -c "claude $*" "$LOG"
