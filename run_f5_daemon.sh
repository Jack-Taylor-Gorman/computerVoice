#!/usr/bin/env bash
# Watchdog wrapper for f5_daemon.py — if the daemon crashes (CUDA OOM,
# socket error, anything), this respawns it after a 5s cooldown. Keeps
# /tmp/majel_f5.sock alive across most failure modes so speak.py never
# silently falls through to the broken RVC stack.
#
# Logs both daemon stdout/stderr and watchdog events to
# /tmp/majel_f5_daemon.log. Stop the watchdog with:
#   pkill -f run_f5_daemon.sh && pkill -f f5_daemon.py
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/tmp/majel_f5_daemon.log

# Ensure we don't pick up a stale socket from a prior run.
rm -f /tmp/majel_f5.sock /tmp/majel_f5_daemon.lock

while true; do
    echo "$(date +%H:%M:%S.%3N) watchdog: launching f5_daemon.py" >>"$LOG"
    "$DIR/venv/bin/python" "$DIR/f5_daemon.py" >>"$LOG" 2>&1
    rc=$?
    echo "$(date +%H:%M:%S.%3N) watchdog: f5_daemon exited rc=$rc, cooldown 5s" >>"$LOG"
    rm -f /tmp/majel_f5.sock /tmp/majel_f5_daemon.lock
    sleep 5
done
