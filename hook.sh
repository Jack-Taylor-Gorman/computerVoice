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
# Tell buddy which terminal this reply came from, so headset Play/Pause
# opens a channel to the session Majel is about to speak about.
printf '%s' "$PAYLOAD" | /home/jackgorman/Desktop/Claude_Projects/buddy/stop_hook.py >/dev/null 2>&1 || true
TRANSCRIPT="$(printf '%s' "$PAYLOAD" | "$DIR/venv/bin/python" -c 'import json,sys; print(json.load(sys.stdin).get("transcript_path",""))')"
[ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ] && exit 0

TEXT="$("$DIR/venv/bin/python" - "$TRANSCRIPT" <<'PY'
# Speak ONLY the assistant text from the CURRENT turn — the reply to the
# most recent human prompt — never the previous interaction's text.
#
# Why this changed: the Stop hook can fire before Claude Code has flushed
# this turn's final assistant text block to the transcript JSONL. The old
# logic ("last assistant text block in the whole file") then picked up the
# PREVIOUS turn's final text, so every spoken synopsis was one interaction
# behind ("for the last interaction, not the current"). Fix: anchor on the
# last genuine human prompt and only consider assistant text that comes
# AFTER it; if that text hasn't landed on disk yet, poll briefly for it
# rather than voicing the stale prior reply. If no current-turn text ever
# appears (e.g. a turn that ended on a tool call), stay silent.
#
# As before, every text block of the chosen assistant message is joined so
# computerize.py can synthesize one Majel-style synopsis of the whole reply
# (invariant 18 already filters discarded intermediate failures).
import json, sys, time

path = sys.argv[1]


def is_human_prompt(o):
    # A real typed prompt — not a tool_result echo, not a sub-agent sidechain.
    if o.get("type") != "user" or o.get("isSidechain"):
        return False
    if "toolUseResult" in o:
        return False
    c = (o.get("message") or {}).get("content")
    if isinstance(c, str):
        return bool(c.strip())
    if isinstance(c, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result"
                       for b in c)
    return False


def current_turn_text():
    try:
        rows = []
        with open(path) as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except OSError:
        return ""
    # Anchor on the last human prompt; fall back to the whole file if none
    # is found (e.g. a resumed/unusual transcript) so we never go silent
    # in cases the old behaviour handled.
    anchor = -1
    for i, o in enumerate(rows):
        if is_human_prompt(o):
            anchor = i
    # The final answer is the assistant text AFTER the turn's last tool
    # activity. A turn is [preamble text] -> [tool calls] -> [final text];
    # the preamble flushes early, so "last assistant text" alone would speak
    # the preamble ("the first line of the response") whenever the real answer
    # has not landed on disk yet. Anchoring past the last tool_use/tool_result
    # skips every preamble and yields nothing until the answer flushes — so the
    # poll waits for it instead of voicing the lead-in.
    after = rows[anchor + 1:]

    def _has_tool(o):
        t = o.get("type")
        cont = (o.get("message") or {}).get("content") or []
        if not isinstance(cont, list):
            return False
        if t == "assistant":
            return any(isinstance(c, dict) and c.get("type") == "tool_use"
                       for c in cont)
        if t == "user":
            return any(isinstance(c, dict) and c.get("type") == "tool_result"
                       for c in cont)
        return False

    last_tool = -1
    for j, o in enumerate(after):
        if not o.get("isSidechain") and _has_tool(o):
            last_tool = j
    final = ""
    for j, o in enumerate(after):
        if j <= last_tool:
            continue
        if o.get("type") != "assistant" or o.get("isSidechain"):
            continue
        blocks = []
        for c in (o.get("message") or {}).get("content", []) or []:
            if isinstance(c, dict) and c.get("type") == "text":
                t = (c.get("text") or "").strip()
                if t:
                    blocks.append(t)
        if blocks:
            final = "\n\n".join(blocks)
    return final


# Poll briefly: this turn's final text block may not be flushed yet.
deadline = time.monotonic() + 3.0
text = current_turn_text()
while not text and time.monotonic() < deadline:
    time.sleep(0.1)
    text = current_turn_text()
print(text)
PY
)"

[ -z "$TEXT" ] && { echo "$(date +%H:%M:%S.%3N) empty TEXT" >>"$LOG"; exit 0; }

# Derive the project name from the transcript path's project-dir slug
# (~/.claude/projects/-home-jackgorman-Desktop-Claude-Projects-computerVoice/...)
# so the rewriter can prepend a "Project <name>." header to every utterance.
PROJECT_NAME="$("$DIR/venv/bin/python" - "$TRANSCRIPT" <<'PY'
import json, os, re, sys
p = sys.argv[1]
# transcript path: .../projects/<slug>/<session>.jsonl
parts = p.split(os.sep)
slug = ""
if "projects" in parts:
    i = parts.index("projects")
    if i + 1 < len(parts):
        slug = parts[i + 1]

# Override sources, in priority order:
#   1. <project_root>/.majel_name file (single line) — checked in to
#      the repo if you want it shared, .gitignored if private.
#   2. ~/.majel_project_names.json — { "<slug>": "Spoken Name" } map,
#      handy for non-clonable folders or when you don't want a file
#      in the repo.
#   3. Auto-derived from the slug's last path component (the original
#      behavior), Title-Cased and camel-split.

real_path = "/" + slug.lstrip("-").replace("-", "/") if slug else ""
name = ""

# 1) Per-project file override.
if real_path and os.path.isdir(real_path):
    cand = os.path.join(real_path, ".majel_name")
    if os.path.exists(cand):
        try:
            name = open(cand).read().strip().splitlines()[0].strip()
        except OSError:
            pass

# 2) Global map override.
if not name:
    cfg_path = os.path.expanduser("~/.majel_project_names.json")
    if os.path.exists(cfg_path):
        try:
            mp = json.load(open(cfg_path))
            name = (mp.get(slug) or "").strip()
        except (json.JSONDecodeError, OSError):
            pass

# 3) Auto-derive fallback.
if not name:
    name = slug.lstrip("-").split("-")[-1] if slug else ""
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
#
# Latest-wins staleness: synthesis+playback can be slower than the gap
# between responses, so utterances pile up behind the flock and you end
# up perpetually hearing an OLDER queued reply ("always one behind").
# To fix that, each response registers a monotonic token in a per-session
# file. speak.py re-reads that file once it acquires the lock; if a newer
# response has registered since, this utterance is stale and drops itself.
# Scoped per session (hash of transcript path) so two terminals finishing
# at once don't cancel each other — only same-session backlog collapses.
REQ_KEY="$(printf '%s' "$TRANSCRIPT" | md5sum | cut -d' ' -f1)"
REQ_FILE="/tmp/majel_speak.latest.$REQ_KEY"
REQ_TOKEN="$(date +%s%N)"
printf '%s' "$REQ_TOKEN" > "$REQ_FILE"
echo "$(date +%H:%M:%S.%3N) queuing speak.py via flock (token=$REQ_TOKEN)" >>"$LOG"

nohup bash -c "MAJEL_LOG=/tmp/majel_speak.log printf '%s' \"\$0\" | MAJEL_LOG=/tmp/majel_speak.log MAJEL_REQ_TOKEN='$REQ_TOKEN' MAJEL_REQ_FILE='$REQ_FILE' flock /tmp/majel_speak.lock '$DIR/venv/bin/python' '$DIR/speak.py' >>/tmp/majel_speak.log 2>&1" "$CLEAN" </dev/null >/dev/null 2>&1 &
disown
exit 0
