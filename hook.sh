#!/usr/bin/env bash
# Stop hook: read last assistant text from transcript, strip, speak via Majel.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG=/tmp/majel_hook.log
exec 2>>"$LOG"
# Suppress ~/.local/site-packages so the venv's CUDA torch is not shadowed by
# a stale CPU torch wheel sitting in user-site.
export PYTHONNOUSERSITE=1
echo "$(date +%H:%M:%S.%3N) hook.sh fired" >>"$LOG"
PAYLOAD="$(cat)"
TRANSCRIPT="$(printf '%s' "$PAYLOAD" | "$DIR/venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("transcript_path",""))')"
[ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ] && exit 0

TEXT="$("$DIR/venv/bin/python" - "$TRANSCRIPT" <<'PY'
# Extract every text block from the last assistant message and join
# them so computerize.py (API mode) can synthesize a single Majel-style
# synopsis of the entire reply.
#
# Why this changed: previous logic took only the LAST text block to
# avoid voicing intermediate-error language. But assistants often emit
# the substantive content first ("Here is what I did. <table>") then a
# short final status line ("Receiver up — PID 1490675.") — taking only
# the last block loses everything except the trailing status.
# Computerize.py's invariant 18 already instructs the rewriter to
# report the FINAL state and skip discarded intermediate failures, so
# joining all blocks is safe — the LLM filters them.
import json, sys
path = sys.argv[1]
last = ""
with open(path) as f:
    for line in f:
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") != "assistant":
            continue
        msg = o.get("message", {})
        blocks: list[str] = []
        for c in msg.get("content", []) or []:
            if isinstance(c, dict) and c.get("type") == "text":
                t = (c.get("text") or "").strip()
                if t:
                    blocks.append(t)
        if blocks:
            last = "\n\n".join(blocks)
print(last)
PY
)"

[ -z "$TEXT" ] && { echo "$(date +%H:%M:%S.%3N) empty TEXT" >>"$LOG"; exit 0; }

# Derive the project name from the transcript path's project-dir slug
# (~/.claude/projects/-home-jackgorman-Desktop-Claude-Projects-computerVoice/...)
# so the rewriter can prepend a "Project <name>." header to every utterance.
PROJECT_NAME="$("$DIR/venv/bin/python" - "$TRANSCRIPT" <<'PY'
import os, re, sys
p = sys.argv[1]
# transcript path: .../projects/<slug>/<session>.jsonl
parts = p.split(os.sep)
slug = ""
if "projects" in parts:
    i = parts.index("projects")
    if i + 1 < len(parts):
        slug = parts[i + 1]
# Slug format: leading "-" then path components joined by "-".
# e.g. "-home-jackgorman-Desktop-Claude-Projects-computerVoice"
# Take the LAST component as the project name.
name = slug.lstrip("-").split("-")[-1] if slug else ""
# Camel/snake/kebab → spaced words, Title-Cased.
name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
name = re.sub(r"[_\-]+", " ", name).strip()
name = " ".join(w.capitalize() for w in name.split())
print(name)
PY
)"
export MAJEL_PROJECT="$PROJECT_NAME"
echo "$(date +%H:%M:%S.%3N) project=$MAJEL_PROJECT" >>"$LOG"

CLEAN="$(printf '%s' "$TEXT" | "$DIR/venv/bin/python" "$DIR/strip.py" | MAJEL_PROJECT="$MAJEL_PROJECT" "$DIR/venv/bin/python" "$DIR/computerize.py")"
[ -z "$CLEAN" ] && { echo "$(date +%H:%M:%S.%3N) empty CLEAN" >>"$LOG"; exit 0; }
echo "$(date +%H:%M:%S.%3N) CLEAN=$CLEAN" >>"$LOG"

# Sequential playback across concurrent Claude sessions: every speak.py
# invocation grabs an exclusive flock on /tmp/majel_speak.lock before it
# starts. flock blocks (no -n), so utterances queue rather than overlap
# or get dropped. The lock auto-releases when speak.py exits.
echo "$(date +%H:%M:%S.%3N) queuing speak.py via flock" >>"$LOG"

nohup bash -c "MAJEL_LOG=/tmp/majel_speak.log printf '%s' \"\$0\" | MAJEL_LOG=/tmp/majel_speak.log flock /tmp/majel_speak.lock '$DIR/venv/bin/python' '$DIR/speak.py' >>/tmp/majel_speak.log 2>&1" "$CLEAN" </dev/null >/dev/null 2>&1 &
disown
exit 0
