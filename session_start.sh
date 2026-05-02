#!/usr/bin/env bash
# SessionStart hook: launch ONLY the current-generation daemons.
#   - voice_sfx.py        SFX clip cache
#   - background.py       crossfaded ambient music loop
#   - f5_daemon.py        F5-TTS voice synthesis (current production backend)
#
# The legacy RVC daemon (majel_daemon.py) is intentionally NOT started here
# anymore — F5 is the current voice and any stray RVC instance was producing
# the "old voice" the user kept hearing. speak.py still has dead-code paths
# that can spin up RVC if its socket is somehow available, but with the
# daemon never auto-launched the F5 path is the only one that fires.
#
# A pre-flight kill removes any zombies from prior versions before we spawn
# the fresh ones, so a stale daemon left over from a long-running session
# can't survive a SessionStart.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SFX_PID="$DIR/.voice_sfx.pid"
BG_PID="$DIR/.background.pid"
F5_PID="$DIR/.majel_f5_daemon.pid"
LOCK="$DIR/.session.lock"

# Drop any prior PID files / sockets / locks for daemons we're about to
# (re)launch. The legacy RVC daemon's lockfile and pidfile are also wiped
# so a stale instance can't claim its socket and impersonate the voice.
rm -f \
    "$DIR/.majel_daemon.pid" \
    /tmp/majel.sock /tmp/majel_daemon.lock \
    /tmp/majel_f5.sock /tmp/majel_f5_daemon.lock 2>/dev/null

# Kill anything already holding our names — covers double-launches across
# concurrent shells and lingering zombies from a prior version.
pkill -f "$DIR/majel_daemon.py" 2>/dev/null
pkill -f "$DIR/f5_daemon.py" 2>/dev/null

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

    if ! ([ -f "$F5_PID" ] && kill -0 "$(cat "$F5_PID")" 2>/dev/null); then
        nohup "$DIR/venv/bin/python" "$DIR/f5_daemon.py" >/tmp/majel_f5_daemon.log 2>&1 &
        echo $! > "$F5_PID"
        disown
    fi
) 9>"$LOCK"

exit 0
