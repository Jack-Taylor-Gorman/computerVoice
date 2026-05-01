# Majel Claude Wrapper — Instant User-Input Alert

The wrapper routes Claude Code through `script -f` so stdout is teed into
`/tmp/claude-stream.log`. A background `tty_watcher.py` process tails that log,
strips ANSI, and fires the user-input alert (`computerbeep_30.mp3`) the moment
Claude prints an approval menu — bypassing Claude's own slow (~6 s) Notification
hook path.

## How to know if you're already inside the wrapper

Run any of these in the session:

```
pgrep -af tty_watcher
pgrep -af "script -f"
ls -la /tmp/claude-stream.log
```

If all three exist, the wrapper is active. If any is missing, alerts fall back
to the slow Notification hook.

## Three ways to start the wrapper

### Option 1 — Desktop shortcut (recommended)

Double-click **Majel-Claude** on your Desktop.
This runs:

```
gnome-terminal -- /home/jackgorman/Desktop/Claude_Projects/computerVoice/start_claude.sh
```

which:
1. Spawns `tty_watcher.py` via `setsid` if not already running.
2. Truncates `/tmp/claude-stream.log`.
3. Execs `script -f -q -c "claude $*" /tmp/claude-stream.log`.

Your terminal behaves normally — `script` tees to both the log and the TTY.

### Option 2 — Shell launch

In any terminal:

```
/exit          # from inside an existing Claude session
/home/jackgorman/Desktop/Claude_Projects/computerVoice/start_claude.sh
```

You can pass flags through: `start_claude.sh --dangerously-skip-permissions` etc.

### Option 3 — Attach watcher only (partial, not recommended)

Useful if you can't restart the current session. Opens a watcher but the
current session still isn't teeing stdout, so alerts won't fire until you
relaunch via option 1 or 2.

```
/home/jackgorman/Desktop/Claude_Projects/computerVoice/venv/bin/python \
  /home/jackgorman/Desktop/Claude_Projects/computerVoice/tty_watcher.py &
```

## What the watcher matches

From `tty_watcher.py`, ANSI-stripped tail of `/tmp/claude-stream.log`:

- `Do you want to proceed?`
- `1. Yes … 2. No`
- `❯ 1. Yes`
- `permission to use`
- `requires your approval`
- `waiting for your input`

Debounce: `DEBOUNCE_S = 2.5` s so redraws don't multi-fire.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| No beep on approval menu | Confirm wrapper active via checks above. Relaunch via option 1. |
| Beep fires twice | Raise `DEBOUNCE_S` in `tty_watcher.py`. |
| Beep fires on normal output | Tighten patterns in `tty_watcher.py`. |
| Terminal scrolling looks weird | `script -f` uses a PTY; some TUI apps render differently. Normal. |
| `/tmp/claude-stream.log` huge | Gets truncated each wrapper launch. Safe to `rm` between sessions. |

## Files

- `start_claude.sh` — wrapper launcher.
- `tty_watcher.py` — log tailer + beep firer.
- `Majel-Claude.desktop` — desktop launcher (copied to `~/Desktop/`).
- `/tmp/claude-stream.log` — live stdout capture (recreated per session).
- `/tmp/tty_watcher.log` — watcher diagnostic output.
