#!/usr/bin/env bash
# SessionEnd hook: stop voice-SFX + background ambient daemons.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for pf in "$DIR/.voice_sfx.pid" "$DIR/.background.pid" "$DIR/.majel_daemon.pid"; do
    if [ -f "$pf" ]; then
        pid="$(cat "$pf")"
        if [ -n "$pid" ]; then
            pkill -P "$pid" 2>/dev/null
            kill "$pid" 2>/dev/null
        fi
        rm -f "$pf"
    fi
done

pkill -f "background.py" 2>/dev/null
exit 0
