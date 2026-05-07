#!/usr/bin/env python3
"""Stuck-loop detector. Watches Claude Code transcript JSONLs in
~/.claude/projects/*/ for tool-use repetition that indicates the agent
is spinning without making progress, and announces the condition in
Majel voice through speak.py.

Detection heuristic:
  - Sliding window of (timestamp, key) tuples per active transcript.
  - key = (tool_name, focus_value):
      Edit/Write/MultiEdit/NotebookEdit → file basename
      Bash                               → first command word
      Read                               → file basename
      others                             → not tracked
  - If WINDOW_SIZE+ identical keys appear within WINDOW_SECS AND no
    progress signal appeared since the loop started, emit one alert
    and put the key into COOLDOWN_SECS so it doesn't re-fire while
    the agent is still stuck.

Progress signals: assistant text containing "complete", "passed",
"merged", "fixed", "done", "success", "resolved", or "nominal".

Toggle via ~/.majel_config.json:loop_watcher_enabled (default False —
opt-in like step narration). The GUI NARRATION section has a pill.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CONFIG = Path.home() / ".majel_config.json"

WINDOW_SECS = 8 * 60      # 8 minutes
WINDOW_SIZE = 5           # 5 same-key tool uses
COOLDOWN_SECS = 5 * 60    # 5 minutes after alert before re-eligible
POLL_INTERVAL = 5         # seconds between polls
ACTIVE_AGE_SECS = 90      # transcripts modified within this window are "active"
LOG = "/tmp/majel_loop_watcher.log"

PROGRESS_RE = re.compile(
    r"\b(complete[d]?|passed|merged|fixed|done|success(?:ful)?|"
    r"resolved|nominal)\b",
    flags=re.IGNORECASE,
)


def _log(msg: str) -> None:
    try:
        with open(LOG, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except OSError:
        pass


def _load_cfg() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _enabled() -> bool:
    cfg = _load_cfg()
    return bool(cfg.get("voice_enabled", True)) and bool(
        cfg.get("loop_watcher_enabled", False)
    )


def _key_for(tool_name: str, tool_input: dict) -> tuple[str, str] | None:
    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        fp = tool_input.get("file_path") or ""
        return (tool_name, os.path.basename(fp))
    if tool_name == "Bash":
        cmd = (tool_input.get("command") or "").strip().split()
        return ("Bash", cmd[0] if cmd else "")
    if tool_name == "Read":
        fp = tool_input.get("file_path") or ""
        return ("Read", os.path.basename(fp))
    return None


def _focus_label(key: tuple[str, str]) -> str:
    tool, focus = key
    if not focus:
        return tool.lower()
    if tool == "Bash":
        return f"{focus} command"
    return focus


def _emit_alert(key: tuple[str, str], count: int) -> None:
    label = _focus_label(key)
    text = (f"Caution. {label} loop detected. "
            f"{count} iterations. No progress detected.")
    _log(f"ALERT: {text}")
    py = ROOT / "venv" / "bin" / "python"
    speak = ROOT / "speak.py"
    if not py.exists() or not speak.exists():
        return
    try:
        cmd = ["flock", "/tmp/majel_speak.lock", str(py), str(speak)]
        env = {**os.environ, "MAJEL_LOG": "/tmp/majel_speak.log"}
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, env=env)
        p.communicate(text.encode(), timeout=120)
    except (subprocess.TimeoutExpired, OSError) as e:
        _log(f"alert dispatch failed: {e}")


class TranscriptState:
    __slots__ = ("tool_log", "cooldown_until", "last_offset",
                 "last_progress_t", "loop_start_t")

    def __init__(self) -> None:
        self.tool_log: deque[tuple[float, tuple[str, str]]] = deque(maxlen=400)
        self.cooldown_until: dict[tuple[str, str], float] = {}
        self.last_offset: int = 0
        self.last_progress_t: float = 0.0
        self.loop_start_t: float = 0.0


def _process_line(line: str, state: TranscriptState) -> None:
    try:
        o = json.loads(line)
    except Exception:
        return
    if o.get("type") != "assistant":
        return
    msg = o.get("message", {})
    now = time.time()
    for c in msg.get("content", []) or []:
        if not isinstance(c, dict):
            continue
        kind = c.get("type")
        if kind == "text":
            t = (c.get("text") or "").strip()
            if t and PROGRESS_RE.search(t):
                state.last_progress_t = now
        elif kind == "tool_use":
            key = _key_for(c.get("name", ""), c.get("input") or {})
            if key:
                state.tool_log.append((now, key))

    if not state.tool_log:
        return
    last_t, last_key = state.tool_log[-1]
    if state.cooldown_until.get(last_key, 0.0) > now:
        return
    cutoff = now - WINDOW_SECS
    matches = [t for t, k in state.tool_log if k == last_key and t >= cutoff]
    if len(matches) < WINDOW_SIZE:
        return
    # No alert if a progress signal arrived after the loop started.
    if state.last_progress_t > matches[0]:
        return
    _emit_alert(last_key, len(matches))
    state.cooldown_until[last_key] = now + COOLDOWN_SECS


def _active_transcripts() -> list[Path]:
    if not CLAUDE_PROJECTS.is_dir():
        return []
    cutoff = time.time() - ACTIVE_AGE_SECS
    out: list[Path] = []
    for proj in CLAUDE_PROJECTS.iterdir():
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            try:
                if f.stat().st_mtime >= cutoff:
                    out.append(f)
            except OSError:
                continue
    return out


def main() -> int:
    _log("loop_watcher started")
    states: dict[Path, TranscriptState] = {}
    while True:
        if not _enabled():
            time.sleep(POLL_INTERVAL)
            continue
        actives = _active_transcripts()
        for path in actives:
            st = states.get(path)
            if st is None:
                st = TranscriptState()
                states[path] = st
                # Watch from end-of-file so we don't re-fire on history.
                try:
                    st.last_offset = path.stat().st_size
                except OSError:
                    st.last_offset = 0
                _log(f"tracking new transcript {path.name}")
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= st.last_offset:
                continue
            try:
                with path.open() as fh:
                    fh.seek(st.last_offset)
                    new_data = fh.read()
                    st.last_offset = fh.tell()
            except OSError:
                continue
            for line in new_data.splitlines():
                if line.strip():
                    _process_line(line, st)
        # Drop transcripts that haven't moved in over an hour.
        active_set = set(actives)
        for p in list(states):
            if p in active_set:
                continue
            recent = states[p].tool_log[-1][0] if states[p].tool_log else 0.0
            if time.time() - recent > 3600:
                states.pop(p, None)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
