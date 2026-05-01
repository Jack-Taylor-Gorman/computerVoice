#!/usr/bin/env bash
# SessionStart hook: launch voice-SFX + background ambient daemons if not already running.
# Uses flock to prevent concurrent SessionStart calls from double-spawning.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SFX_PID="$DIR/.voice_sfx.pid"
BG_PID="$DIR/.background.pid"
DAEMON_PID="$DIR/.majel_daemon.pid"
LOCK="$DIR/.session.lock"

(
    flock -n 9 || exit 0

    if ! ([ -f "$SFX_PID" ] && kill -0 "$(cat "$SFX_PID")" 2>/dev/null); then
        nohup "$DIR/venv/bin/python" "$DIR/voice_sfx.py" >/dev/null 2>&1 &
        echo $! > "$SFX_PID"
        disown
    fi

    if ! ([ -f "$BG_PID" ] && kill -0 "$(cat "$BG_PID")" 2>/dev/null); then
        nohup "$DIR/venv/bin/python" "$DIR/background.py" >/dev/null 2>&1 &
        echo $! > "$BG_PID"
        disown
    fi

    if ! ([ -f "$DAEMON_PID" ] && kill -0 "$(cat "$DAEMON_PID")" 2>/dev/null); then
        nohup "$DIR/venv/bin/python" "$DIR/majel_daemon.py" >/tmp/majel_daemon.log 2>&1 &
        echo $! > "$DAEMON_PID"
        disown
    fi
) 9>"$LOCK"

exit 0
