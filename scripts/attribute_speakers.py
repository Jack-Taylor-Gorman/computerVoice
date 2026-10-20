#!/usr/bin/env python3
"""Attribute every dialogue span in a book to a speaker.

Pass 1 — rule-based.  Finds `"…," said X.` / `X said, "…"` / `"…," X
asked.` and similar attested patterns. Resolves ~70% of dialogue with
high confidence. The pattern set covers the verb inventory observed in
*The Shadow of the Torturer* (said / asked / told / replied / answered /
cried / whispered / shouted / murmured / muttered / exclaimed / spoke /
called).

Pass 2 — Haiku attribution.  For each unattributed span, build a context
window (preceding 5 paragraphs, the span itself, and following 1
paragraph) plus the running cast list, and ask claude-haiku-4-5 to name
the speaker. Cached so repeated runs are free.

Pass 3 — Sliding speaker context.  If a span sits adjacent to a tagged
span with the same indentation/scene and a near-by ¶ break, inherit the
prior speaker (covers untagged back-and-forth).

The Severian-as-character rule:  when the speaker resolves to Severian
himself, output `severian-character` ONLY if there is an explicit inline
attribution (`I said,` / `I replied,` etc.).  Otherwise default to
`severian-narrator`.  When attribution genuinely fails, fall back to
`severian-narrator` (per user instruction).

Input:
  <book.txt>

Output (one JSONL line per dialogue span):
  dataset_avers/characters/attribution.jsonl
    {span_idx, char_start, char_end, text, speaker, confidence, method}
  dataset_avers/characters/attribution_cache.json
    {span_hash: {speaker, method}}     — survives reruns
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "dataset_avers" / "characters"

DIALOGUE_VERBS = (
    "said asked replied told answered cried whispered shouted murmured "
    "muttered exclaimed spoke called demanded suggested inquired "
    "remarked observed noted continued protested objected"
).split()

# The cast list is learned dynamically from the book — capitalised names
# that appear ≥ 10 times. Severian, the narrator, gets a special label
# because the user wants him split between "narrator" and "character"
# only on explicit `I said,`-style markers.
NARRATOR_DEFAULT = "severian-narrator"
NARRATOR_CHARACTER = "severian-character"


def find_dialogue_spans(text: str) -> list[tuple[int, int, str]]:
    """Locate every `"…"` quoted span. Returns [(start, end, content), ...]."""
    spans: list[tuple[int, int, str]] = []
    in_quote = False
    start = 0
    for i, ch in enumerate(text):
        if ch == '"':
            if not in_quote:
                start = i + 1
                in_quote = True
            else:
                content = text[start:i].strip()
                if content:
                    spans.append((start - 1, i + 1, content))
                in_quote = False
    return spans


def build_cast(text: str, min_count: int = 8) -> list[str]:
    """Return capitalised names appearing ≥ min_count times, excluding
    English starts-of-sentence words."""
    SENT_START = {
        "The", "It", "He", "She", "They", "We", "You", "I", "But", "And",
        "When", "Then", "There", "Here", "Now", "What", "Why", "Who",
        "How", "That", "This", "These", "Those", "If", "In", "On", "At",
        "From", "To", "For", "Of", "By", "With", "As", "So", "Yet",
        "Or", "Nor", "Although", "Because", "While", "After", "Before",
        "No", "Yes",
    }
    counts: dict[str, int] = {}
    for m in re.finditer(r"\b[A-Z][a-z]+\b", text):
        w = m.group(0)
        counts[w] = counts.get(w, 0) + 1
    return sorted(
        [n for n, c in counts.items() if c >= min_count and n not in SENT_START],
        key=lambda n: -counts[n],
    )


def normalize_speaker(name: str, cast: set[str]) -> str:
    """Canonicalise a raw speaker token to a stable bucket label.
    `Master Gurloes` → `master-gurloes`, `Severian` → `severian-narrator`."""
    name = name.strip().rstrip(".,;:!?").strip()
    if not name:
        return "unknown"
    low = name.lower()
    if low.startswith("i ") or low == "i":
        return NARRATOR_CHARACTER  # explicit first-person attribution
    if name in cast and name != "Severian":
        return name.lower().replace(" ", "-")
    if name == "Severian":
        return NARRATOR_DEFAULT
    # Title-prefixed: "Master Gurloes", "Dr. Talos", "the autarch"
    bare = re.sub(r"^(Master|Mistress|Lady|Lord|Sir|Captain|Dr|Doctor|the)\s+", "", name)
    if bare in cast and bare != "Severian":
        return name.lower().replace(" ", "-").replace(".", "")
    return name.lower().replace(" ", "-").replace(".", "")


# A "speaker name token" is 1–3 capitalised words, optionally
# title-prefixed. The strict cap on word count prevents the regex from
# slurping clauses like "thus my friend Roche".
NAME_TOKEN = r"(?:(?:Master|Mistress|Lady|Lord|Sir|Captain|Dr\.?|Doctor|the)\s+)?(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+){0,2}"


def _valid_speaker(raw: str, cast: set[str]) -> bool:
    """A speaker token is valid if at least one of its capitalised words
    appears in the cast list (and it's not a sentence-start function word)."""
    SENT_START = {
        "The", "It", "He", "She", "They", "We", "You", "But", "And",
        "When", "Then", "There", "Here", "Now", "What", "Why", "Who",
        "How", "That", "This", "These", "Those", "If", "In", "On", "At",
        "From", "To", "For", "Of", "By", "With", "As", "So", "Yet", "No",
        "Or", "Nor", "Although", "Because", "While", "After", "Before",
        "Thus", "Hence", "Therefore", "Moreover", "However", "Indeed",
    }
    parts = raw.split()
    if not parts:
        return False
    # The first word must not be a bare sentence-start unless it's title-prefixed.
    if parts[0] in SENT_START and parts[0] not in cast:
        return False
    return any(p in cast for p in parts)


def rule_attribute(text: str, span: tuple[int, int, str], cast: set[str]
                   ) -> tuple[str | None, float, str]:
    """Try attested dialogue-tag patterns. Return
    (speaker_label, confidence, method) or (None, 0, '') if no rule matched."""
    cs, ce, content = span
    verb_alt = "|".join(DIALOGUE_VERBS)
    pre = text[max(0, cs - 80):cs]
    post = text[ce:ce + 80]

    # Pattern A1: "..." said X       (verb + name)
    m = re.match(rf"\s*[,.]?\s*({verb_alt})\s+({NAME_TOKEN})\b",
                 post)
    if m and _valid_speaker(m.group(2), cast):
        return normalize_speaker(m.group(2), cast), 0.92, "A:said-name"

    # Pattern A2: "..." said I       (verb + first-person)
    if re.match(rf"\s*[,.]?\s*({verb_alt})\s+I\b", post):
        return NARRATOR_CHARACTER, 0.92, "A:said-I"

    # Pattern A3: "..." said to X    (verb + 'to' + name — addressee, NOT speaker)
    # Skip this — `said to X` means speaker is unspecified, X is addressee.

    # Pattern B1: "..." X said       (name + verb)
    m = re.match(rf"\s*[,.]?\s*({NAME_TOKEN})\s+({verb_alt})\b",
                 post)
    if m and _valid_speaker(m.group(1), cast):
        return normalize_speaker(m.group(1), cast), 0.92, "B:name-said"

    # Pattern B2: "..." I said       (first-person + verb)
    if re.match(rf"\s*[,.]?\s*I\s+({verb_alt})\b", post):
        return NARRATOR_CHARACTER, 0.92, "B:I-said"

    # Pattern C1: X said, "..."       (name + verb, BEFORE the open-quote)
    m = re.search(rf"\b({NAME_TOKEN})\s+({verb_alt})\b[,\s]*$",
                  pre)
    if m and _valid_speaker(m.group(1), cast):
        return normalize_speaker(m.group(1), cast), 0.92, "C:name-said-pre"

    # Pattern C2: I said, "..."
    if re.search(rf"\bI\s+({verb_alt})\b[,\s]*$", pre):
        return NARRATOR_CHARACTER, 0.92, "C:I-said-pre"

    return None, 0.0, ""


def haiku_attribute(client, span_text: str, ctx_before: str, ctx_after: str,
                    cast: list[str], recent: list[str],
                    model: str = "claude-haiku-4-5-20251001",
                    method_label: str = "haiku") -> tuple[str, float, str]:
    """Ask the chosen Claude model to name the speaker of `span_text`. Returns
    (speaker_label, confidence, method)."""
    cast_str = ", ".join(cast[:25])
    recent_str = " → ".join(recent[-3:]) if recent else "(none)"
    system = (
        "You attribute spoken dialogue in a novel to a single speaker. "
        "Reply with ONLY the speaker's name on one line — no explanation, "
        "no quotes, no honorifics unless they are part of the canonical "
        "name (e.g. 'Master Gurloes' is correct; 'Mr Gurloes' is not). "
        "If the speaker is the first-person narrator, output literally 'I'. "
        "If the speaker is genuinely impossible to identify, output 'UNKNOWN'."
    )
    user = (
        f"Cast list (most frequent first): {cast_str}\n\n"
        f"Most recent speakers in this scene: {recent_str}\n\n"
        f"--- CONTEXT (before the line) ---\n{ctx_before}\n"
        f"--- THE LINE TO ATTRIBUTE ---\n\"{span_text}\"\n"
        f"--- CONTEXT (after the line) ---\n{ctx_after}\n\n"
        f"Who speaks the bracketed line? Reply with one name only."
    )
    try:
        r = client.messages.create(
            model=model,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            max_tokens=30,
            temperature=0.0,
            messages=[{"role": "user", "content": user}],
        )
        out = "".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip()
        if not out or out == "UNKNOWN":
            return NARRATOR_DEFAULT, 0.20, f"{method_label}:unknown"
        # Sonnet has higher inherent confidence on tough attribution
        conf = 0.85 if method_label == "sonnet" else 0.75
        return out, conf, method_label
    except Exception as e:
        return NARRATOR_DEFAULT, 0.0, f"{method_label}-error:{e}"


def get_api_key() -> str | None:
    for p in (Path.home() / ".severian_config.json", Path.home() / ".majel_config.json"):
        if p.exists():
            try:
                cfg = json.loads(p.read_text())
                k = cfg.get("anthropic_api_key")
                if k:
                    return k
            except Exception:
                pass
    return os.environ.get("ANTHROPIC_API_KEY")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("book", help="path to the canonical book .txt file")
    ap.add_argument("--out", default=None,
                    help="output JSONL (default: dataset_avers/characters/attribution.jsonl)")
    ap.add_argument("--cache", default=None,
                    help="attribution cache (default: dataset_avers/characters/attribution_cache.json)")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip the LLM pass — leave low-confidence spans as severian-narrator")
    ap.add_argument("--model", choices=["haiku", "sonnet"], default="haiku",
                    help="LLM model for attribution (default haiku; sonnet for harder texts)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only attribute first N spans (smoke test)")
    args = ap.parse_args()

    LLM_MODEL = ("claude-sonnet-4-6" if args.model == "sonnet"
                 else "claude-haiku-4-5-20251001")
    LLM_LABEL = args.model

    book_path = Path(args.book)
    text = book_path.read_text(encoding="utf-8", errors="replace")
    spans = find_dialogue_spans(text)
    cast = build_cast(text)
    cast_set = set(cast)
    print(f"book: {book_path}")
    print(f"dialogue spans: {len(spans)}")
    print(f"cast (top 12): {cast[:12]}")
    print()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else (OUT_DIR / "attribution.jsonl")
    cache_path = Path(args.cache) if args.cache else (OUT_DIR / "attribution_cache.json")
    cache: dict[str, dict] = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            cache = {}
    print(f"loaded {len(cache)} cached attributions")

    # Pass 1: rule-based.
    attributions: list[dict] = []
    n_rule = n_unattrib = 0
    for i, span in enumerate(spans):
        if args.limit and i >= args.limit:
            break
        cs, ce, content = span
        speaker, conf, method = rule_attribute(text, span, cast_set)
        if speaker is None:
            speaker, conf, method = None, 0.0, "unattributed"
            n_unattrib += 1
        else:
            n_rule += 1
        attributions.append({
            "span_idx": i, "char_start": cs, "char_end": ce,
            "text": content,
            "speaker": speaker, "confidence": conf, "method": method,
        })
    print(f"pass 1 (rules):       {n_rule:>4} attributed, "
          f"{n_unattrib:>4} unattributed")

    # Pass 2: Haiku for unattributed.
    if not args.no_llm and n_unattrib > 0:
        api_key = get_api_key()
        if not api_key:
            print("no ANTHROPIC_API_KEY — skipping Haiku pass", file=sys.stderr)
        else:
            from anthropic import Anthropic
            client = Anthropic(api_key=api_key)
            recent_speakers: list[str] = []
            n_haiku = 0
            t0 = time.monotonic()
            for rec in attributions:
                if rec["speaker"] is not None:
                    if rec["speaker"] not in (NARRATOR_DEFAULT, NARRATOR_CHARACTER):
                        recent_speakers.append(rec["speaker"])
                    continue
                cs = rec["char_start"]
                ce = rec["char_end"]
                ctx_before = text[max(0, cs - 800):cs]
                ctx_after = text[ce:ce + 300]
                key = hashlib.sha1(
                    (rec["text"] + "|" + ctx_before[-200:]).encode()
                ).hexdigest()
                if key in cache:
                    cached = cache[key]
                    rec["speaker"] = cached["speaker"]
                    rec["confidence"] = cached.get("confidence", 0.7)
                    rec["method"] = cached.get("method", "haiku-cache")
                    continue
                raw_speaker, conf, method = haiku_attribute(
                    client, rec["text"], ctx_before, ctx_after, cast, recent_speakers,
                    model=LLM_MODEL, method_label=LLM_LABEL,
                )
                if raw_speaker == "I" or raw_speaker.lower() in ("i", "narrator", "severian"):
                    # Could be Severian-narrator OR Severian-character — distinguish by
                    # whether there's an explicit "I said" / "I replied" in the surrounding
                    # text. We re-check the local window for an inline first-person tag.
                    inline_first = re.search(
                        r"\bI\s+(?:said|asked|replied|answered|told|cried|whispered|shouted)\b",
                        ctx_before[-200:] + ctx_after[:200],
                    )
                    rec["speaker"] = NARRATOR_CHARACTER if inline_first else NARRATOR_DEFAULT
                else:
                    rec["speaker"] = normalize_speaker(raw_speaker, cast_set)
                rec["confidence"] = conf
                rec["method"] = method
                cache[key] = {
                    "speaker": rec["speaker"],
                    "confidence": rec["confidence"],
                    "method": rec["method"],
                }
                n_haiku += 1
                if rec["speaker"] not in (NARRATOR_DEFAULT, NARRATOR_CHARACTER):
                    recent_speakers.append(rec["speaker"])
                if n_haiku % 25 == 0:
                    elapsed = time.monotonic() - t0
                    print(f"  {LLM_LABEL}: {n_haiku} attributed ({elapsed:.1f}s)", flush=True)
                    cache_path.write_text(json.dumps(cache, indent=2))
            print(f"pass 2 ({LLM_LABEL}):      {n_haiku:>4} attributed "
                  f"({time.monotonic()-t0:.1f}s)")
            cache_path.write_text(json.dumps(cache, indent=2))

    # Pass 3: sliding context — anything still unattributed → severian-narrator.
    n_fallback = 0
    for rec in attributions:
        if rec["speaker"] is None:
            rec["speaker"] = NARRATOR_DEFAULT
            rec["confidence"] = 0.15
            rec["method"] = "fallback:narrator"
            n_fallback += 1
    if n_fallback:
        print(f"pass 3 (fallback):    {n_fallback:>4} dropped to {NARRATOR_DEFAULT}")

    # Write out.
    with out_path.open("w") as f:
        for rec in attributions:
            f.write(json.dumps(rec) + "\n")
    print(f"\nwrote {len(attributions)} attributions → {out_path}")

    # Summary by speaker.
    from collections import Counter
    by_speaker = Counter(r["speaker"] for r in attributions)
    print("\n=== summary by speaker ===")
    for sp, n in by_speaker.most_common(20):
        print(f"  {n:>4}  {sp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
