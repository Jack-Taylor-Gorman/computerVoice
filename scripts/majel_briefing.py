#!/usr/bin/env python3
"""Project briefing mode — give the computer a full read of the project
state, then have her deliver a Majel-voice mission briefing covering:

  1. PROJECT STATUS REPORT       — what's operational, what's recent
  2. MISSION OBJECTIVE           — what we're trying to achieve
  3. OPEN FAULTS                 — TODOs, FIXMEs, uncommitted changes
  4. RECOMMENDED COURSES OF ACTION — 3-5 creative next steps

Usage:
  scripts/majel_briefing.py              # speak it (default)
  scripts/majel_briefing.py --print      # print, don't synthesize
  scripts/majel_briefing.py --text-only  # dump raw context, no Claude
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Project-context files we read verbatim if they exist.
CONTEXT_FILES = [
    "README.md", "README.rst", "README.txt",
    "CONTEXT.md", "GOALS.md", "ROADMAP.md", "MAJEL_VOICE_GUIDE.md",
    "CLAUDE.md", "AGENTS.md",
]

# Source extensions we sweep for TODO/FIXME comments.
SCAN_EXTS = (".py", ".sh", ".md", ".js", ".ts", ".rs", ".go")
SCAN_PATTERNS = ["TODO", "FIXME", "XXX", "HACK"]


def _run(args: list[str], cwd: Path | None = None) -> str:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=20)
        return r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def gather_context() -> str:
    parts: list[str] = [f"=== PROJECT ROOT: {ROOT.name} ==="]

    parts.append("\n=== TOP-LEVEL ENTRIES ===")
    try:
        for entry in sorted(ROOT.iterdir()):
            if entry.name.startswith("."):
                continue
            tag = "/" if entry.is_dir() else ""
            parts.append(f"  {entry.name}{tag}")
    except OSError:
        pass

    for fname in CONTEXT_FILES:
        p = ROOT / fname
        if p.exists() and p.is_file():
            txt = p.read_text(errors="ignore")[:6000]
            parts.append(f"\n=== {fname} ===\n{txt}")

    log = _run(["git", "log", "--oneline", "-40"], cwd=ROOT)
    if log:
        parts.append(f"\n=== RECENT COMMITS (last 40) ===\n{log}")

    diff_stat = _run(["git", "diff", "--stat", "HEAD"], cwd=ROOT)
    if diff_stat:
        parts.append(f"\n=== UNCOMMITTED DIFF (stat) ===\n{diff_stat}")

    status = _run(["git", "status", "--short", "--branch"], cwd=ROOT)
    if status:
        parts.append(f"\n=== GIT STATUS ===\n{status}")

    todos: list[str] = []
    for pat in SCAN_PATTERNS:
        out = _run(
            ["grep", "-rn", "--include=*.py", "--include=*.sh", "--include=*.md",
             "--include=*.ts", "--include=*.js", pat, "."],
            cwd=ROOT,
        )
        if out.strip():
            todos.append(out)
    if todos:
        joined = "\n".join(todos)
        # Cap at 6000 chars so a noisy codebase doesn't blow context.
        parts.append(f"\n=== OPEN TODOS / FIXMES (capped) ===\n{joined[:6000]}")

    # Quick line-count summary so the model has a size signal.
    py_files = list(ROOT.rglob("*.py"))
    py_lines = sum(p.read_text(errors="ignore").count("\n")
                   for p in py_files if "venv" not in p.parts and "site-packages" not in p.parts)
    parts.append(f"\n=== SIZE ===\n{len(py_files)} python files, ~{py_lines} lines")

    return "\n".join(parts)


BRIEFING_PROMPT = """You are the Star Trek ship's computer (Majel Barrett voice). The user is the captain of a development project. Your task: deliver a comprehensive briefing of the project's state in computer-speak. The full project context is provided below by the user.

The briefing must contain four sections, in order, each starting with a labelled sentence:

SECTION 1 — "Project status report."
Current operational state. Which subsystems work. Which are under development. Files / components recently modified. Active branches. Length: 4–6 sentences.

SECTION 2 — "Mission objective."
The goal. What this project is trying to achieve. Target completion criteria. Use any README, CONTEXT, GOALS, ROADMAP, or CLAUDE.md content as authoritative. Length: 3–5 sentences.

SECTION 3 — "Open faults."
Outstanding issues — TODO/FIXME items, uncommitted changes, known bugs, partial features. Use the lists provided. Length: 3–6 sentences.

SECTION 4 — "Recommended courses of action."
Three to five concrete next steps the captain could pursue. Be creative — suggest experiments, refactors, features, or research that would advance the mission. Use the option-enumeration form: "Option one, [action]. Option two, [action]." etc. End with a single imperative ("Specify selection.") so the captain can choose.

Voice rules (apply throughout):
- No contractions. No "I". No "we". No pleasantries.
- Declarative, period-separated sentences. One thought per sentence.
- Exact numerals over qualitative words.
- Label-first alerts where applicable ("Warning. ...").
- When asking the user to specify something AND a recommendation exists, the imperative ("Specify selection.") comes BEFORE the recommendation ("Recommend option two, [reason]."), as separate sentences.
- Total length: 250–500 words. Concise but complete.

Output ONLY the briefing — no preamble, no markdown, no headers, no quotes, no bullet symbols. Use plain sentences ending with "." or "!" or "?"."""


def call_claude(context: str, key: str) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=key)
    r = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        temperature=0.3,
        system=[{"type": "text", "text": BRIEFING_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": context}],
    )
    return "".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip()


def chunk_for_speak(text: str, max_chars: int = 1600) -> list[str]:
    """Split the briefing on sentence boundaries into chunks small enough
    that speak.py's MAX_CHARS doesn't truncate. Each chunk is queued via
    the existing flock so they play sequentially."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    cur = ""
    for s in sentences:
        if len(cur) + len(s) + 1 > max_chars and cur:
            chunks.append(cur.strip())
            cur = s
        else:
            cur = (cur + " " + s).strip() if cur else s
    if cur:
        chunks.append(cur.strip())
    return chunks


def speak(chunk: str) -> None:
    """Pipe a chunk through the flock-queued speak.py."""
    py = ROOT / "venv" / "bin" / "python"
    cmd = ["flock", "/tmp/majel_speak.lock", str(py), str(ROOT / "speak.py")]
    env = {**os.environ, "MAJEL_LOG": "/tmp/majel_speak.log"}
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, env=env)
    p.communicate(chunk.encode())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="Print the briefing; don't synthesize it.")
    ap.add_argument("--text-only", action="store_true",
                    help="Dump the raw context and exit; skip Claude.")
    args = ap.parse_args()

    cfg_path = Path.home() / ".majel_config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    key = os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key")

    sys.stderr.write("[gathering project context…]\n")
    context = gather_context()
    sys.stderr.write(f"[context: {len(context)} chars]\n")

    if args.text_only:
        print(context)
        return 0

    if not key:
        sys.stderr.write("no anthropic_api_key in env or ~/.majel_config.json\n")
        return 2

    sys.stderr.write("[calling Claude…]\n")
    briefing = call_claude(context, key)
    if not briefing:
        sys.stderr.write("empty briefing from Claude\n")
        return 2

    # Run through the standard pronunciation post-processor so acronyms,
    # versions, and build numbers get the same treatment as normal hooks.
    try:
        import computerize  # noqa: WPS433 — late import is intentional
        briefing_processed = computerize._post_process(briefing)
    except Exception:
        briefing_processed = briefing

    print(briefing_processed)

    if args.print_only:
        return 0

    chunks = chunk_for_speak(briefing_processed)
    sys.stderr.write(f"[speaking {len(chunks)} chunk(s)…]\n")
    for c in chunks:
        speak(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
