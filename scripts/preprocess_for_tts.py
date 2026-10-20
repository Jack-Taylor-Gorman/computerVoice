#!/usr/bin/env python3
"""Preprocess a story for multi-voice TTS rendering.

Three passes:
  1. STATIC heteronym rewrite — replace ambiguous spellings with phonetic
     forms based on attested context patterns (live, read, lead, etc.).
  2. LLM heteronym pass — Sonnet rewrites residual ambiguous words with
     a 3-sentence window for context. Cached per-hash so re-runs are free.
  3. Interjection/pacing detection — regex flags short emphatic spans
     (Hah!, Yes!, Lord!) as candidates for fast-read at synth time.

Output:
  <stem>.cleaned.txt              text with heteronyms rewritten
  <stem>.pacing.jsonl             one record per detected interjection:
                                    {start_char, end_char, text, speed}
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


# ── Pass 1: static heteronym rewrite ─────────────────────────────────────
# Each rule: (regex, replacement). Order matters — longest/most-specific first.
# These are surface-spelling rewrites that change nothing semantically but
# nudge F5-TTS toward the right phoneme.
HETERONYM_RULES: list[tuple[str, str]] = [
    # "live" — adj/broadcast (lyve) vs verb (lihv).
    # F5 reads bare "live" as the verb. Force the long-i in adj/broadcast contexts.
    (r"\blive\s+(wire|wires|broadcast|stream|streaming|television|tv|"
     r"performance|concert|recording|action|event|audience)\b", r"lyve \1"),
    (r"\b(was|is|are|going|stays?|remained?)\s+live\b", r"\1 lyve"),
    (r"\blive\s+(stage|broadcast)\b", r"lyve \1"),
    # Long-i for "alive" already correct — skip.

    # "read" past-tense (red) vs present (reed).
    (r"\b(have|has|had|already|just|previously|never|once)\s+read\b", r"\1 red"),
    (r"\bread\s+(the\s+book|the\s+letter|it|him|her|aloud)\s+(yesterday|earlier|last|once)\b",
     r"red \1 \2"),
    (r"\bI\s+read\s+(it|that|this)\s+(yesterday|earlier|once)\b", r"I red \1 \2"),
    # "had read" / "have read" forms
    (r"\b(I|he|she|they|we|you)\s+had\s+read\b", r"\1 had red"),

    # "lead" metal (led) vs verb (leed).
    (r"\blead\s+(solder|pipe|pipes|paint|poisoning|acid|battery|"
     r"shielding|sheet|weight|weights|crystal|glass)\b", r"led \1"),
    (r"\b(of|piece of|made of|in)\s+lead\b", r"\1 led"),

    # "wound" past tense of wind (wownd) vs injury (woond — fine as is).
    (r"\bwound\s+(up|around|tight|round|back)\b", r"wownd \1"),
    (r"\b(had|has|have)\s+wound\b", r"\1 wownd"),

    # "bow" — front of ship / weapon arch (boh) vs greeting (bow short-ow).
    (r"\bbow\s+(of\s+the\s+ship|of\s+the\s+boat|section|thrusters?|wave|tie)\b",
     r"boh \1"),
    (r"\b(stern\s+and\s+)?bow\s+of\b", r"\1boh of"),
    (r"\b(arrow\s+and\s+|drew\s+his?\s+|drew\s+her\s+|drew\s+the\s+)bow\b",
     r"\1boh"),

    # "tear" — rip (tair) vs eye-drop (teer).
    (r"\btear\s+in\s+the\s+(hull|fabric|page|file|paper|cloth|sail)\b",
     r"tair in the \1"),
    (r"\btear\s+(it|this|that)\s+(off|up|away|in)\b", r"tair \1 \2"),
    (r"\btear\s+open\b", r"tair open"),

    # "minute" adjective (my-noot — small) vs noun (min-it — 60s).
    (r"\bminute\s+(detail|details|difference|differences|amount|amounts|"
     r"change|changes|fluctuation|fluctuations|particle|particles|trace)\b",
     r"my-noot \1"),

    # "close" adj (cloce) vs verb (cloze).
    # Both are common; only rewrite if a clear adj context.
    (r"\b(very|so|too)\s+close\s+(to|by|together)\b", r"\1 cloce \2"),
    (r"\bclose\s+(call|quarters?|range|friend|friends|friendship)\b",
     r"cloce \1"),

    # "wind" — air (wind) vs verb (waind). Rare in narrative prose; skip
    # unless we hit problems. (F5 usually gets these right.)

    # "use" noun (yooss) vs verb (yooze).
    (r"\bof\s+use\s+(to|for|in)\b", r"of yooss \1"),
    (r"\bany\s+use\b", r"any yooss"),
    (r"\bno\s+use\b", r"no yooss"),

    # "house" noun (howse) vs verb (howze).
    (r"\bhouse\s+(them|him|her|us|me)\b", r"howze \1"),

    # "refuse" noun (REF-yoos — garbage) vs verb (re-FYOOZ — decline).
    (r"\b(the|some|much|piles?\s+of)\s+refuse\b", r"\1 ref-yoos"),

    # "polish" Polish adj (polish — capitalised) vs verb (PAHL-ish).
    # Handled by capitalisation in most fonts; F5 usually OK. Skip.

    # Numbered phrases F5 mangles:
    # "Aught" / "ought" — quick fix.
    # "GHB" / weird acronyms — out of scope here.

    # Stand-alone interjections that F5 stretches awkwardly:
    # We don't rewrite these in pass 1 — pass 3 marks them for fast-speed.
]

_HETERONYM_RES: list[tuple[re.Pattern, str]] = [
    (re.compile(p, flags=re.IGNORECASE), r) for p, r in HETERONYM_RULES
]


def apply_static_heteronyms(text: str) -> tuple[str, int]:
    """Return (rewritten, num_changes)."""
    out = text
    n = 0
    for pat, rep in _HETERONYM_RES:
        new_out, count = pat.subn(rep, out)
        if count:
            n += count
            out = new_out
    return out, n


# ── Pass 3: interjection detection ───────────────────────────────────────
INTERJECTION_WORDS = ("Hah", "Ha", "Lord", "God", "Yes", "No", "Oh", "Ah",
                       "Bah", "Wait", "Stop", "Now", "Look", "Right", "Hark")
# Either standalone-quoted ("Hah!") OR inline-quoted (within a dialogue line:
# Hah. Hah! Hah?). The inline form is what Wolfe actually uses for verbal tics.
INTERJECTION_RE = re.compile(
    r'\b(' + '|'.join(INTERJECTION_WORDS) + r')([!?.])(?=\s|$|"|—|,)',
)


def detect_interjections(text: str) -> list[dict]:
    """Find short emphatic interjections, including ones embedded mid-line
    (Wolfe's 'Hah!' / 'Hah.' verbal-tic pattern). Mark each as a short text
    token that the chunker/synth should pace faster and tail-trim so F5
    doesn't drag the syllable out into a freaky boom."""
    out = []
    for m in INTERJECTION_RE.finditer(text):
        out.append({
            "char_start": m.start(),
            "char_end": m.end(),
            "text": m.group(0),  # e.g. "Hah!" / "Hah." / "Lord!"
            "speed": 0.95,
            "trim_tail_ms": 150,  # post-synth tail trim — kills the long boom
            "reason": "interjection-short-emphatic",
        })
    return out


# ── Pass 2: LLM heteronym pass ───────────────────────────────────────────
# Words that the static map can't disambiguate without semantic context.
# Sonnet inspects a 3-sentence window per word and decides which spelling.
AMBIGUOUS_WORDS = (
    "live read lead bow tear wound minute close use does present record "
    "object subject content desert refuse contract project conduct produce "
    "permit progress address content"
).split()


def get_api_key() -> str | None:
    for p in (Path.home() / ".severian_config.json", Path.home() / ".majel_config.json"):
        if p.exists():
            try:
                cfg = json.loads(p.read_text())
                if cfg.get("anthropic_api_key"):
                    return cfg["anthropic_api_key"]
            except Exception:
                pass
    return os.environ.get("ANTHROPIC_API_KEY")


def split_sentences(text: str) -> list[str]:
    abbrev = re.compile(
        r"\b(?:Mr|Mrs|Ms|Dr|St|Sr|Jr|vs|etc|e\.g|i\.e|cf|Capt|Lt|Col|Gen|"
        r"Rev|Prof|Sgt|Pvt|Cpl|Mt|Ft|Co|Ltd|Inc)\.",
        re.IGNORECASE,
    )
    masked = abbrev.sub(lambda m: m.group(0).replace(".", "§"), text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", masked)
    return [p.replace("§", ".").strip() for p in parts if p.strip()]


def llm_heteronym_pass(text: str, cache_path: Path) -> tuple[str, int]:
    """For each AMBIGUOUS_WORD in text that's not already disambiguated,
    send Sonnet a 3-sentence window and ask for the corrected spelling.
    Returns (rewritten_text, num_changes). Cached per-window-hash."""
    api_key = get_api_key()
    if not api_key:
        print("  no API key — skipping LLM pass", file=sys.stderr)
        return text, 0

    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            cache = {}

    sentences = split_sentences(text)
    # Build sentence-index map of each ambiguous word
    candidates: list[tuple[int, int, str]] = []  # (sent_idx, word_idx, word)
    word_re = re.compile(r"\b(" + "|".join(AMBIGUOUS_WORDS) + r")\b", re.IGNORECASE)
    for si, sent in enumerate(sentences):
        for m in word_re.finditer(sent):
            # Skip if already rewritten by static pass (token contains a phonetic spelling)
            # The static pass produces tokens like "lyve", "red", "led" — not in AMBIGUOUS_WORDS.
            candidates.append((si, m.start(), m.group(0)))

    if not candidates:
        return text, 0
    print(f"  LLM pass: {len(candidates)} ambiguous-word instances in {len(sentences)} sentences")

    try:
        from anthropic import Anthropic
    except ImportError:
        return text, 0
    client = Anthropic(api_key=api_key)

    # Send each sentence ONCE if it contains any candidates — saves API calls.
    rewrites: dict[int, str] = {}  # sent_idx → rewritten sentence
    sent_indices = sorted({c[0] for c in candidates})

    for si in sent_indices:
        # 3-sentence window for context
        before = sentences[max(0, si - 1):si]
        target = sentences[si]
        after = sentences[si + 1:si + 2]
        window_key = "\n".join(before + [target] + after)
        h = hashlib.sha1(window_key.encode()).hexdigest()
        if h in cache:
            rewrites[si] = cache[h]
            continue

        system = (
            "You are a TTS pronunciation editor. Rewrite ambiguous English words "
            "in the TARGET line so a text-to-speech model says the correct phoneme. "
            "Apply these substitutions ONLY when context makes the pronunciation clear:\n"
            "  'live' (adj/broadcast, long-i) → 'lyve'\n"
            "  'read' (past tense) → 'red'\n"
            "  'lead' (metal noun) → 'led'\n"
            "  'wound' (past of wind) → 'wownd'\n"
            "  'bow' (ship/weapon) → 'boh'\n"
            "  'tear' (rip) → 'tair'\n"
            "  'minute' (small) → 'my-noot'\n"
            "  'close' (near, adj) → 'cloce'\n"
            "  'use' (noun) → 'yooss'\n"
            "  'house' (verb) → 'howze'\n"
            "  'refuse' (garbage) → 'ref-yoos'\n"
            "  'does' (female deer) → 'doze'\n"
            "  'record' (noun, REC-erd) → 'rek-erd'\n"
            "  'present' (noun/adj) → 'prez-ent'\n"
            "  'object' (noun) → 'OB-ject'\n"
            "  'subject' (noun) → 'SUB-ject'\n"
            "  'content' (adj, happy) → 'cun-TENT'\n"
            "  'content' (noun, stuff) → 'CON-tent'\n"
            "  'desert' (sand) → 'DEZ-ert'\n"
            "  'desert' (abandon) → 'di-ZERT'\n"
            "  'contract' (verb) → 'cun-TRACT'\n"
            "  'project' (verb) → 'pro-JECT'\n"
            "  'conduct' (verb) → 'cun-DUCT'\n"
            "  'produce' (verb) → 'pro-DOOCE'\n"
            "  'permit' (verb) → 'per-MIT'\n"
            "  'progress' (verb) → 'pro-GRESS'\n"
            "  'address' (verb) → 'ad-DRESS'\n"
            "Leave all other words unchanged. Preserve every capital letter, punctuation, "
            "and quote mark. Output ONLY the rewritten TARGET line — no explanation, "
            "no quotes, no preface. If no substitution applies, output the TARGET unchanged."
        )
        user = (
            f"BEFORE: {' '.join(before) if before else '(start)'}\n"
            f"TARGET: {target}\n"
            f"AFTER:  {' '.join(after) if after else '(end)'}\n\n"
            f"Rewrite TARGET:"
        )
        try:
            r = client.messages.create(
                model="claude-sonnet-4-6",
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                max_tokens=400,
                temperature=0.0,
                messages=[{"role": "user", "content": user}],
            )
            out = "".join(b.text for b in r.content if getattr(b, "type", None) == "text").strip()
            # Sanity: same length-ish?
            if not out or abs(len(out) - len(target)) > 50:
                out = target  # fallback to original
            rewrites[si] = out
            cache[h] = out
        except Exception as e:
            print(f"  llm error on sent {si}: {e}", file=sys.stderr)
            rewrites[si] = target

    # Save cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2))

    # Reassemble. Use the original text and replace each sentence's contents.
    # We split by sentence boundaries, swap in rewrites by index.
    out_sentences = sentences.copy()
    for si, new_sent in rewrites.items():
        out_sentences[si] = new_sent
    n_changed = sum(1 for si, new in rewrites.items() if new != sentences[si])
    print(f"  LLM rewrote {n_changed} sentences")

    # Rejoin with single space — we'll lose original paragraph breaks here.
    # To preserve paragraph structure, rejoin per-paragraph instead.
    # For simplicity in this first version, we accept losing paragraph breaks
    # inside the LLM-touched text and let downstream re-detect them by
    # patterns. The pacing JSONL still works because it's char-offset based.
    return " ".join(out_sentences), n_changed


# ── main ─────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="path to source text")
    ap.add_argument("--out-dir", default=None,
                    help="output dir (default: same dir as input)")
    ap.add_argument("--no-llm", action="store_true",
                    help="skip Sonnet pass (use static rules only)")
    ap.add_argument("--cache", default=None,
                    help="LLM heteronym cache path (default: <stem>.cache.json)")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"missing: {src}", file=sys.stderr); return 2
    out_dir = Path(args.out_dir) if args.out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_clean = out_dir / f"{src.stem}.cleaned.txt"
    out_pacing = out_dir / f"{src.stem}.pacing.jsonl"
    cache_path = Path(args.cache) if args.cache else (out_dir / f"{src.stem}.cache.json")

    text = src.read_text(encoding="utf-8", errors="replace")
    print(f"input:  {src}  ({len(text):,} chars)")

    # Pass 1: static heteronyms
    print("[1/3] static heteronym rewrite")
    text, n_static = apply_static_heteronyms(text)
    print(f"      {n_static} static rewrites")

    # Pass 2: LLM heteronyms
    if not args.no_llm:
        print("[2/3] LLM heteronym pass (Sonnet)")
        t0 = time.monotonic()
        text, n_llm = llm_heteronym_pass(text, cache_path)
        print(f"      {n_llm} sentences rewritten ({time.monotonic()-t0:.1f}s)")
    else:
        print("[2/3] LLM pass skipped (--no-llm)")

    # Pass 3: interjection detection (run AFTER rewrites so char offsets match output)
    print("[3/3] interjection detection")
    pacing = detect_interjections(text)
    print(f"      {len(pacing)} interjections flagged for fast-read")

    # Write
    out_clean.write_text(text, encoding="utf-8")
    with out_pacing.open("w") as f:
        for p in pacing:
            f.write(json.dumps(p) + "\n")
    print(f"\nwrote:")
    print(f"  {out_clean}")
    print(f"  {out_pacing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
