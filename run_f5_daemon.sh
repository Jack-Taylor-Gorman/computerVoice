#!/usr/bin/env bash
# Watchdog wrapper for f5_daemon.py.
#
# Two failure modes are covered:
#   1. Daemon process crashes (CUDA OOM, exception) - bash sees non-zero
#      exit and we respawn after a 5s cooldown.
#   2. Daemon process stays alive but /tmp/majel_f5.sock disappears
#      (systemd-tmpfiles cleanup, manual rm). bash never sees an exit,
#      so we poll the socket every 5s and kill+respawn when it's gone.
#      Without this, speak.py keeps hitting "daemon unavailable" and
#      every Stop hook degrades to the alert tone instead of voicing.
#
# Logs daemon stdout/stderr and watchdog events to /tmp/majel_f5_daemon.log.
# Stop the watchdog with: pkill -f run_f5_daemon.sh && pkill -f f5_daemon.py
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/tmp/majel_f5_daemon.log
SOCK=/tmp/majel_f5.sock
LOCK=/tmp/majel_f5_daemon.lock
STARTUP_GRACE=60      # seconds we allow the model to load before socket-monitoring kicks in
POLL_INTERVAL=5       # seconds between socket-existence checks

rm -f "$SOCK" "$LOCK"

while true; do
    echo "$(date +%H:%M:%S.%3N) watchdog: launching f5_daemon.py" >>"$LOG"
    "$DIR/venv/bin/python" "$DIR/f5_daemon.py" >>"$LOG" 2>&1 &
    pid=$!

    # Give the daemon time to load the model and bind the socket before
    # we start enforcing socket presence.
    waited=0
    while [ $waited -lt $STARTUP_GRACE ]; do
        kill -0 "$pid" 2>/dev/null || break
        [ -S "$SOCK" ] && break
        sleep 1
        waited=$((waited + 1))
    done

    # Active monitoring: kill the daemon if its socket vanishes.
    while kill -0 "$pid" 2>/dev/null; do
        if [ ! -S "$SOCK" ]; then
            echo "$(date +%H:%M:%S.%3N) watchdog: socket vanished, killing pid=$pid for respawn" >>"$LOG"
            kill "$pid" 2>/dev/null
            sleep 1
            kill -9 "$pid" 2>/dev/null
            break
        fi
        sleep $POLL_INTERVAL
    done

    wait "$pid" 2>/dev/null
    rc=$?
    echo "$(date +%H:%M:%S.%3N) watchdog: f5_daemon exited rc=$rc, cooldown 5s" >>"$LOG"
    rm -f "$SOCK" "$LOCK"
    sleep 5
done
