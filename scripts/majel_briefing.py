#!/usr/bin/env python3
"""Project briefing mode — give the computer a full read of any project
and have her deliver a Majel-voice mission briefing.

Two depth levels:
  --mode full   (default) Status, objective, faults, options. ~250-500w.
  --mode quick  Recent developments only — last commits, current diff,
                README/explainer skim. ~150-250w.

By default the briefing covers this project. Pass --project /path/to/dir
to brief any other directory. Markdown / explainer files (README.md,
CONTEXT.md, GOALS.md, ROADMAP.md, CLAUDE.md, AGENTS.md) get prioritized
context for both modes.

Usage:
  scripts/majel_briefing.py                              # full, this project
  scripts/majel_briefing.py --mode quick                 # recent developments
  scripts/majel_briefing.py --print                      # don't synthesize
  scripts/majel_briefing.py --project /path/to/repo      # different project
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Every briefing — full text + the input-context fingerprint — is appended
# to BRIEFINGS_LOG and saved as an individual markdown so the user can
# diff "what's changed since last time". Both files live under dataset/
# and are gitignored by default (they may contain in-flight project prose).
BRIEFINGS_DIR = ROOT / "dataset" / "briefings"
BRIEFINGS_LOG = BRIEFINGS_DIR / "log.jsonl"

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


def gather_context(project: Path, mode: str) -> str:
    """Build the context string for Claude. Different modes pull different
    slices: full pulls everything, recent skips deep history, momentum
    focuses on commit velocity and recent activity."""
    parts: list[str] = [f"=== PROJECT ROOT: {project.name} ==="]
    parts.append(f"=== BRIEFING MODE: {mode} ===")

    parts.append("\n=== TOP-LEVEL ENTRIES ===")
    try:
        for entry in sorted(project.iterdir()):
            if entry.name.startswith("."):
                continue
            tag = "/" if entry.is_dir() else ""
            parts.append(f"  {entry.name}{tag}")
    except OSError:
        pass

    if mode == "full":
        # Pull every recognized context/explainer file in full.
        for fname in CONTEXT_FILES:
            p = project / fname
            if p.exists() and p.is_file():
                txt = p.read_text(errors="ignore")[:6000]
                parts.append(f"\n=== {fname} ===\n{txt}")
        # Also walk top-level .md files we don't recognize, capped, so
        # arbitrary projects with their own docs file get covered.
        seen = {f.lower() for f in CONTEXT_FILES}
        for md in sorted(project.glob("*.md")):
            if md.name.lower() in seen:
                continue
            try:
                parts.append(f"\n=== {md.name} (extra) ===\n{md.read_text(errors='ignore')[:3000]}")
            except OSError:
                pass
    else:
        # Quick mode: pull the first README-flavored file for orientation
        # plus any top-level *.md so we don't miss the project's own
        # explainer with a non-standard name (e.g. "VOICE_GUIDE.md").
        for fname in CONTEXT_FILES[:3]:
            p = project / fname
            if p.exists() and p.is_file():
                txt = p.read_text(errors="ignore")[:2500]
                parts.append(f"\n=== {fname} (excerpt) ===\n{txt}")
                break
        for md in sorted(project.glob("*.md"))[:3]:
            try:
                parts.append(f"\n=== {md.name} (excerpt) ===\n{md.read_text(errors='ignore')[:1500]}")
            except OSError:
                pass

    if mode == "full":
        log = _run(["git", "log", "--oneline", "-40"], cwd=project)
        if log:
            parts.append(f"\n=== RECENT COMMITS (last 40) ===\n{log}")
    else:  # quick
        log = _run(["git", "log", "--oneline", "-15"], cwd=project)
        if log:
            parts.append(f"\n=== RECENT COMMITS (last 15) ===\n{log}")

    diff_stat = _run(["git", "diff", "--stat", "HEAD"], cwd=project)
    if diff_stat:
        parts.append(f"\n=== UNCOMMITTED DIFF (stat) ===\n{diff_stat}")
    status = _run(["git", "status", "--short", "--branch"], cwd=project)
    if status:
        parts.append(f"\n=== GIT STATUS ===\n{status}")

    if mode == "full":
        todos: list[str] = []
        for pat in SCAN_PATTERNS:
            out = _run(
                ["grep", "-rn", "--include=*.py", "--include=*.sh", "--include=*.md",
                 "--include=*.ts", "--include=*.js", pat, "."],
                cwd=project,
            )
            if out.strip():
                todos.append(out)
        if todos:
            joined = "\n".join(todos)
            parts.append(f"\n=== OPEN TODOS / FIXMES (capped) ===\n{joined[:6000]}")

    if mode == "full":
        py_files = list(project.rglob("*.py"))
        py_lines = sum(p.read_text(errors="ignore").count("\n")
                       for p in py_files
                       if "venv" not in p.parts and "site-packages" not in p.parts)
        parts.append(f"\n=== SIZE ===\n{len(py_files)} python files, ~{py_lines} lines")

    return "\n".join(parts)


_VOICE_RULES = """Voice rules (apply throughout):
- No contractions. No "I". No "we". No pleasantries.
- Declarative, period-separated sentences. One thought per sentence.
- Exact numerals over qualitative words.
- Label-first alerts where applicable ("Warning. ...").
- When asking the user to specify something AND a recommendation exists, the imperative ("Specify selection.") comes BEFORE the recommendation ("Recommend option two, [reason]."), as separate sentences.
Output ONLY the briefing — no preamble, no markdown, no headers, no quotes, no bullet symbols. Use plain sentences ending with "." or "!" or "?"."""


PROMPT_FULL = f"""You are the Star Trek ship's computer (Majel Barrett voice). The user is the captain of a development project. Your task: deliver a comprehensive briefing of the project's state in computer-speak. The full project context is provided below by the user.

The briefing must contain four sections, in order, each starting with a labelled sentence:

SECTION 1 — "Project status report."
Current operational state. Which subsystems work. Which are under development. Files / components recently modified. Active branches. Length: 4–6 sentences.

SECTION 2 — "Mission objective."
The goal. What this project is trying to achieve. Target completion criteria. Use any README, CONTEXT, GOALS, ROADMAP, or CLAUDE.md content as authoritative. Length: 3–5 sentences.

SECTION 3 — "Open faults."
Outstanding issues — TODO/FIXME items, uncommitted changes, known bugs, partial features. Use the lists provided. Length: 3–6 sentences.

SECTION 4 — "Recommended courses of action."
Three to five concrete next steps the captain could pursue. Be creative — suggest experiments, refactors, features, or research that would advance the mission. Use the option-enumeration form: "Option one, [action]. Option two, [action]." etc. End with a single imperative ("Specify selection.") so the captain can choose.

Total length: 250–500 words. Concise but complete.

{_VOICE_RULES}"""


PROMPT_QUICK = f"""You are the Star Trek ship's computer (Majel Barrett voice). The captain has requested a QUICK BRIEFING — recent developments only, no full mission objective recap. Assume the captain knows the project.

Single section, starting with the labelled sentence:

"Quick briefing."

Cover: what shipped in the most recent commits (3-5 sentences), what is in flight as uncommitted work (2-3 sentences), and one short closing sentence about whether the trajectory is toward completion or new scope expansion. Total length: 150–250 words.

{_VOICE_RULES}"""


PROMPTS = {
    "full": PROMPT_FULL,
    "quick": PROMPT_QUICK,
    # Back-compat aliases for the prior --mode values.
    "recent": PROMPT_QUICK,
    "momentum": PROMPT_QUICK,
}


def save_briefing(project: Path, mode: str, briefing: str, context: str) -> Path:
    """Persist a briefing to dataset/briefings/. Returns the markdown
    path. Both the per-briefing markdown AND a JSONL log row are written
    so the user can diff history later.

    Markdown filename:  YYYY-MM-DDTHH-MM-SSZ_<project-slug>_<mode>.md
    JSONL row fields:   ts, project, mode, context_hash, briefing,
                        prompt_version, model
    """
    BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%SZ")
    slug = re.sub(r"[^\w]+", "-", project.name).strip("-") or "unknown"
    md_path = BRIEFINGS_DIR / f"{ts}_{slug}_{mode}.md"
    ctx_hash = hashlib.sha1(context.encode("utf-8", "ignore")).hexdigest()[:12]

    md_path.write_text(
        f"# Briefing — {project.name} · {mode}\n\n"
        f"- Generated: {ts}\n"
        f"- Project: `{project}`\n"
        f"- Mode: {mode}\n"
        f"- Context fingerprint: `{ctx_hash}`\n\n"
        f"---\n\n{briefing}\n",
        encoding="utf-8",
    )
    row = {
        "ts": ts,
        "project": str(project),
        "project_name": project.name,
        "mode": mode,
        "context_hash": ctx_hash,
        "briefing": briefing,
        "model": "claude-haiku-4-5-20251001",
        "md_path": str(md_path.relative_to(ROOT)),
    }
    with BRIEFINGS_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return md_path


def call_claude(context: str, key: str, mode: str) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=key)
    prompt = PROMPTS.get(mode, PROMPT_FULL)
    r = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        temperature=0.3,
        system=[{"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}}],
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
    ap.add_argument("--mode", choices=["full", "quick", "recent", "momentum"],
                    default="full",
                    help="Briefing depth: full or quick. (recent/momentum kept as aliases for the old API.)")
    ap.add_argument("--project", type=Path, default=ROOT,
                    help="Project directory to brief (default: this project).")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="Print the briefing; don't synthesize it.")
    ap.add_argument("--text-only", action="store_true",
                    help="Dump the raw context and exit; skip Claude.")
    args = ap.parse_args()

    project = args.project.expanduser().resolve()
    if not project.is_dir():
        sys.stderr.write(f"not a directory: {project}\n")
        return 2

    cfg_path = Path.home() / ".majel_config.json"
    cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    key = os.environ.get("ANTHROPIC_API_KEY") or cfg.get("anthropic_api_key")

    sys.stderr.write(f"[gathering context: project={project.name} mode={args.mode}]\n")
    context = gather_context(project, args.mode)
    sys.stderr.write(f"[context: {len(context)} chars]\n")

    if args.text_only:
        print(context)
        return 0

    if not key:
        sys.stderr.write("no anthropic_api_key in env or ~/.majel_config.json\n")
        return 2

    sys.stderr.write(f"[calling Claude — mode={args.mode}…]\n")
    briefing = call_claude(context, key, args.mode)
    if briefing:
        try:
            md = save_briefing(project, args.mode, briefing, context)
            sys.stderr.write(f"[saved → {md.relative_to(ROOT)}]\n")
        except OSError as e:
            sys.stderr.write(f"[save failed: {e}]\n")
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
