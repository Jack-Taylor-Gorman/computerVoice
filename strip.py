#!/usr/bin/env python3
"""Strip markdown/code/paths/URLs from assistant prose before TTS."""
import re
import sys


def strip(text: str) -> str:
    t = text
    # Code blocks (fenced + inline).
    t = re.sub(r"```.*?```", " ", t, flags=re.DOTALL)
    t = re.sub(r"`[^`]+`", " ", t)
    # Markdown image / link.
    t = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", t)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    # URLs.
    t = re.sub(r"https?://\S+", " ", t)
    # Filesystem paths and file-extension nouns.
    t = re.sub(r"(?:^|\s)(?:[/~]|\./|\.\./)[^\s`'\"]+", " ", t)
    t = re.sub(r"\b[\w.-]+\.(?:py|js|ts|tsx|jsx|md|json|yaml|yml|toml|sh|rs|go|java|c|cpp|h|hpp|html|css|sql|txt|log|cfg|ini|lock|pth|zip)\b(?::\d+)?", " ", t)
    # Markdown emphasis.
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"(?<!\*)\*([^*\n]+)\*", r"\1", t)
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"^\s*\d+\.\s+", "", t, flags=re.MULTILINE)
    # Bare punctuation that has no spoken form. F5 voices these
    # phonetically as the WORD ("bracket", "parenthesis", "equals")
    # which is never what we want — the rewriter wouldn't have written
    # them if it meant the word, only the symbol.
    t = re.sub(r"[_~<>|()\[\]{}=&@#\$\^\\/+]", " ", t)
    # Mathematical / typographic glyphs F5 also tends to verbalize.
    t = re.sub(r"[≤≥≠≈±×÷™®©°§¶†‡]", " ", t)
    # Smart quotes → ASCII quotes (TTS reads them better).
    t = re.sub(r"[“”„]", '"', t)
    t = re.sub(r"[‘’‚]", "'", t)
    # ── Pronunciation-artefact filters (post-strip cleanup) ──────────
    # Arrows + bullet glyphs that F5 reads phonetically as "p" / weird
    # artefacts. Replace with a sentence-pause comma so cadence stays.
    t = re.sub(r"[→←↑↓↔⇒⇐⇔»«›‹▶◀●○◆◇■□▪▫•·]", ", ", t)
    # En- and em-dashes — same artefact risk as arrows. Convert to comma.
    t = re.sub(r"\s*[—–−]\s*", ", ", t)
    # Stray emoji / pictograph code points.
    t = re.sub(
        r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]",
        " ", t,
    )
    # Commit-hash-like tokens — 7+ hex chars surrounded by word boundary
    # AND containing at least one digit (so "Acknowledged" stays safe).
    t = re.sub(r"\b(?=[a-f0-9]*\d)[a-f0-9]{7,}\b", " ", t)
    # Lone single ASCII letters between whitespace ("P" floating alone)
    # — these are almost always artefacts (bullet leftover, contraction
    # remnant, an arrow's nearby fragment). Keep "I" and "A" since they
    # are legitimate English words.
    t = re.sub(r"(?<=\s)(?!I\b|A\b)[A-Za-z](?=\s)", " ", t)
    # Final whitespace + duplicate-punctuation collapse.
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    t = re.sub(r"([,.;:!?])\1+", r"\1", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


if __name__ == "__main__":
    data = sys.stdin.read()
    out = strip(data)
    if len(out) < 3:
        sys.exit(0)
    sys.stdout.write(out)
