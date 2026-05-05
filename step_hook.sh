#!/usr/bin/env bash
# PostToolUse hook: narrate substantive tool calls in Majel voice while
# the assistant is still working, not just at end-of-turn.
#
# Wire this in ~/.claude/settings.json under PostToolUse:
#   { "matcher": "Edit|Write|NotebookEdit|MultiEdit|Bash", "hooks": [
#       { "type": "command",
#         "command": "/home/jackgorman/Desktop/Claude_Projects/computerVoice/step_hook.sh" } ] }
#
# Toggleable from the LCARS GUI (NARRATION pill) — when
# narrate_during_build is false in ~/.majel_config.json, this exits
# silently with no audio.
#
# Throttle: skips if the last narration fired less than NARRATE_MIN_GAP
# seconds ago, so a burst of edits doesn't produce a wall of speech.
# The throttle file is read+written under an exclusive flock so two
# concurrent PostToolUse hooks can't both pass the gate.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/tmp/majel_step.log
exec 2>>"$LOG"
export PYTHONNOUSERSITE=1

NARRATE_MIN_GAP="${NARRATE_MIN_GAP:-25}"  # seconds between narrations

# Read the entire JSON payload from stdin as raw bytes. Pass to the
# Python child via environment variable — NEVER via shell-string
# interpolation into a heredoc. The payload comes from the model's
# tool_input which can contain arbitrary characters, including triple
# quotes and backslashes; interpolating those into Python source is a
# sandbox escape ("""+__import__('os').system('id')+"""). Env vars
# are bytes and Python reads them as opaque strings, so injection is
# impossible by construction.
PAYLOAD="$(cat)"

NARRATION="$(MAJEL_PAYLOAD="$PAYLOAD" \
              MAJEL_NARRATE_MIN_GAP="$NARRATE_MIN_GAP" \
              "$DIR/venv/bin/python" - <<'PY'
import fcntl, json, os, sys, time
payload_raw = os.environ.get("MAJEL_PAYLOAD", "")
try:
    payload = json.loads(payload_raw)
except Exception:
    sys.exit(0)

# 1) Master switch.
cfg_path = os.path.expanduser("~/.majel_config.json")
cfg = {}
if os.path.exists(cfg_path):
    try:
        cfg = json.load(open(cfg_path))
    except Exception:
        pass
if not cfg.get("voice_enabled", True):
    sys.exit(0)
if not cfg.get("narrate_during_build", False):
    sys.exit(0)

# 2) Tool filter — only narrate substantive actions. Run filter BEFORE
#    the throttle so non-substantive tools don't even consume the
#    throttle window (otherwise a burst of Read calls could starve the
#    next real Edit narration).
tool = (payload.get("tool_name") or "").strip()
SUBSTANTIVE = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
if tool not in SUBSTANTIVE and tool != "Bash":
    sys.exit(0)

ti = payload.get("tool_input") or {}
file_path = ti.get("file_path") or ""
brief_input = ""
if tool in {"Edit", "MultiEdit"}:
    brief_input = f"Edit {os.path.basename(file_path)}"
elif tool == "Write":
    brief_input = f"Write {os.path.basename(file_path)}"
elif tool == "NotebookEdit":
    brief_input = f"NotebookEdit {os.path.basename(file_path)}"
elif tool == "Bash":
    cmd = (ti.get("command") or "").strip()
    desc = (ti.get("description") or "").strip()[:140]
    if not desc:
        sys.exit(0)
    # Trivial-command denylist — these are ergonomic noise even when the
    # assistant labelled them. "cd /path", "pwd", "ls", "which X", "echo
    # ..." narrations are pure clutter.
    first = cmd.split() and cmd.split()[0] or ""
    TRIVIAL = {"cd", "pwd", "ls", "which", "echo", "true", "false", ":"}
    if first in TRIVIAL:
        sys.exit(0)
    brief_input = f"Bash: {desc}"

# 3) Throttle — under an exclusive flock so two concurrent PostToolUse
#    invocations cannot both pass the gate. The lock auto-releases on
#    process exit. Timestamp is written BEFORE the LLM call so the
#    next race competitor sees a fresh ts even if our LLM call hangs.
ts_path = "/tmp/majel_step_last.ts"
lock_path = "/tmp/majel_step.lock"
gap = float(os.environ.get("MAJEL_NARRATE_MIN_GAP", "25"))
lock_fh = open(lock_path, "w")
try:
    fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(0)
now = time.time()
last = 0.0
try:
    last = float(open(ts_path).read().strip() or 0)
except Exception:
    pass
if now - last < gap:
    sys.exit(0)
try:
    open(ts_path, "w").write(str(now))
except Exception:
    pass
# Don't release the flock until process exit — keeps any LLM-stalled
# concurrent hook from sneaking in just because we wrote the ts.

# 4) Intent context — most recent assistant text BEFORE this tool use,
#    so the narration captures the model's stated reason ("Now I'll fix
#    the auth bug" → narration leads with that).
transcript = payload.get("transcript_path") or ""
recent_text = ""
try:
    if transcript and os.path.exists(transcript):
        with open(transcript) as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("type") != "assistant":
                    continue
                msg = o.get("message", {})
                for c in msg.get("content", []) or []:
                    if isinstance(c, dict) and c.get("type") == "text":
                        t = (c.get("text") or "").strip()
                        if t:
                            recent_text = t
except Exception:
    pass

# Trim recent_text to one or two sentences for context.
sentences = recent_text.replace("\n", " ").split(". ")
recent_brief = ". ".join(sentences[-2:])[:300]

# 5) Compose the input prose for the rewriter.
prose = f"{recent_brief}\n\n[Action in progress] {brief_input}"
print(prose)
PY
)"

# Empty narration → bail.
[ -z "$NARRATION" ] && exit 0

# Run through the rewriter with NARRATE_STEP env so it produces a short
# in-progress sentence rather than a full end-of-turn report. Then queue
# through the same flock-protected speak.py path so step narrations
# never overlap end-of-turn narrations.
CLEAN="$(printf '%s' "$NARRATION" | MAJEL_NARRATE_STEP=1 "$DIR/venv/bin/python" "$DIR/computerize.py")"
[ -z "$CLEAN" ] && exit 0

echo "$(date +%H:%M:%S.%3N) STEP=$CLEAN" >>"$LOG"

nohup bash -c "MAJEL_LOG=/tmp/majel_speak.log printf '%s' \"\$0\" | flock /tmp/majel_speak.lock '$DIR/venv/bin/python' '$DIR/speak.py' >>/tmp/majel_speak.log 2>&1" "$CLEAN" </dev/null >/dev/null 2>&1 &
disown
exit 0
