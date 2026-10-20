#!/usr/bin/env python3
"""Detect and remove character-dialogue clips from the Avers ranked set.

Roy Avers narrates the *Book of the New Sun* audiobook AND voices its
characters — Master Gurloes, Drotte, Thecla, etc. His timbre shifts when
he's in-character. For a clean narrator-voice F5 finetune we want only
the narration, not the voiced dialogue, otherwise the model occasionally
slips into a character timbre at synth time.

This script reads `dataset_avers/clips_ranked/_metadata.jsonl` (full
WhisperX transcripts), scores each clip on text-based dialogue heuristics,
and — when run with --apply — moves the high-confidence dialogue clips
to `dataset_avers/clips_trash/` and appends to `manifest_deleted.jsonl`.

Heuristics (precision-favoring; we keep ambiguous clips):
  + 0.40   quotation mark anywhere in transcript
  + 0.30   contains dialogue tag (" said,  said\\b, asked, replied, told)
  + 0.20   starts with a conversational imperative ("Come", "Listen",
           "Tell me", "Hush", "Wait", "Stop", "Look")
  + 0.15   ends with conversational shape (?  !)  but only if no narration
           anchor noun present
  + 0.15   second-person address ("you have", "you are", "your", "thou")
           AND the clip is short (< 100 chars)
  - 0.20   contains a narration anchor — past-tense verb + 3rd-person
           pronoun ("she said", "he turned", "they walked"), descriptive
           phrases, or Severian's "I remember" reflection.

Threshold default 0.50. Tune with --threshold.

Usage:
    ./scripts/filter_dialogue.py                 # dry-run, prints stats
    ./scripts/filter_dialogue.py --apply         # actually move clips
    ./scripts/filter_dialogue.py --threshold 0.40 --apply
    ./scripts/filter_dialogue.py --show 20       # show first N flagged
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RANKED_DIR = ROOT / "dataset_avers" / "clips_ranked"
METADATA = RANKED_DIR / "_metadata.jsonl"
TRASH_DIR = ROOT / "dataset_avers" / "clips_trash"
MANIFEST_DELETE = ROOT / "dataset_avers" / "manifest_deleted.jsonl"

# Quotation marks — straight + curly + guillemets.
QUOTE_RE = re.compile(r'["“”«»‹›„‚]')
SINGLE_QUOTE_DIALOGUE_RE = re.compile(r"(^|\s)'[A-Z]")

DIALOGUE_TAG_RE = re.compile(
    r"\b(said|asked|replied|told|answered|cried|whispered|shouted|"
    r"murmured|sighed|exclaimed|muttered|spoke)\b",
    re.IGNORECASE,
)

IMPERATIVE_OPENERS = (
    "come", "listen", "tell me", "hush", "wait", "stop", "look",
    "hark", "behold", "see", "speak", "answer", "no, no", "please",
    "let me", "let us", "do not", "don't", "won't you", "will you",
    "what is", "what are", "who", "why", "how can",
)

NARRATION_ANCHORS_RE = re.compile(
    r"\b(?:"
    r"(?:he|she|they|it|the\s+\w+)\s+(?:was|were|had|did|said|"
    r"turned|walked|stood|sat|rose|came|went|saw|knew|thought|"
    r"remembered|seemed|appeared|spoke)|"
    r"i\s+(?:remembered|knew|thought|saw|recalled|recall|remember|"
    r"could see|had seen|felt|believed|understood)|"
    r"(?:upon|amid|amongst|across|within|beneath|above|beyond|toward|"
    r"through)\s+the|"
    r"(?:in\s+)?those\s+(?:days|times|hours|years)|"
    r"(?:that|this)\s+(?:was|seemed|appeared|had)|"
    r"(?:the|a|an)\s+\w+\s+(?:of|in|on|with)\s+the"
    r")\b",
    re.IGNORECASE,
)


def starts_with_imperative(text: str) -> bool:
    low = text.lower().lstrip(" \"'“‘")
    return any(low.startswith(opener) for opener in IMPERATIVE_OPENERS)


def score_dialogue(text: str) -> tuple[float, list[str]]:
    """Return (score, signals) for a transcript."""
    if not text:
        return 0.0, []
    s = 0.0
    sigs: list[str] = []

    if QUOTE_RE.search(text):
        s += 0.40
        sigs.append("quote-mark")
    elif SINGLE_QUOTE_DIALOGUE_RE.search(text):
        s += 0.30
        sigs.append("single-quote-dialogue")

    if DIALOGUE_TAG_RE.search(text):
        s += 0.30
        sigs.append("dialogue-tag")

    if starts_with_imperative(text):
        s += 0.20
        sigs.append("imperative-start")

    # End-shape heuristic — only fires if no narration anchor.
    ends_conversational = text.rstrip().endswith(("?", "!"))
    has_anchor = bool(NARRATION_ANCHORS_RE.search(text))
    if ends_conversational and not has_anchor:
        s += 0.15
        sigs.append("conversational-ending")

    # Short second-person addresses ("you have been very wicked.").
    if (len(text) < 100 and
            re.search(r"\b(?:you|your|thou|thy|thee)\b", text, re.IGNORECASE)):
        s += 0.15
        sigs.append("second-person-short")

    if has_anchor:
        s -= 0.20
        sigs.append("narration-anchor")

    return max(0.0, min(1.0, s)), sigs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--threshold", type=float, default=0.50,
                    help="flag clips with dialogue score >= threshold (default 0.50)")
    ap.add_argument("--apply", action="store_true",
                    help="actually move the wavs (without it, dry-run only)")
    ap.add_argument("--show", type=int, default=10,
                    help="show first N flagged samples (default 10)")
    ap.add_argument("--show-borderline", action="store_true",
                    help="also show borderline clips just under the threshold")
    args = ap.parse_args()

    if not METADATA.exists():
        print(f"missing {METADATA} — run scripts/rank_avers.py first", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for line in METADATA.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    flagged: list[dict] = []
    borderline: list[dict] = []
    score_buckets = {"0.0-0.2": 0, "0.2-0.4": 0, "0.4-0.5": 0,
                     "0.5-0.7": 0, "0.7-0.9": 0, ">=0.9": 0}

    for r in rows:
        ds, sigs = score_dialogue(r.get("text", ""))
        r["dialogue_score"] = round(ds, 3)
        r["dialogue_signals"] = sigs
        if ds < 0.2:   score_buckets["0.0-0.2"] += 1
        elif ds < 0.4: score_buckets["0.2-0.4"] += 1
        elif ds < 0.5: score_buckets["0.4-0.5"] += 1
        elif ds < 0.7: score_buckets["0.5-0.7"] += 1
        elif ds < 0.9: score_buckets["0.7-0.9"] += 1
        else:          score_buckets[">=0.9"] += 1
        if ds >= args.threshold:
            flagged.append(r)
        elif ds >= max(0.0, args.threshold - 0.15):
            borderline.append(r)

    print(f"\nanalysed {len(rows)} clips · threshold {args.threshold:.2f}")
    print("dialogue score distribution:")
    for k, v in score_buckets.items():
        print(f"  {k:>9s}: {v}")
    print(f"\nflagged as character dialogue:  {len(flagged)} clips")
    if borderline:
        print(f"borderline (just under threshold): {len(borderline)} clips")

    if args.show > 0 and flagged:
        print(f"\nfirst {min(args.show, len(flagged))} flagged samples:")
        for r in flagged[:args.show]:
            sigs = ",".join(r["dialogue_signals"])
            print(f"  [{r['dialogue_score']:.2f}] {r['filename']}")
            print(f"         {r['text'][:140]!r}")
            print(f"         signals: {sigs}")

    if args.show_borderline and borderline:
        print(f"\nborderline samples:")
        for r in borderline[:args.show]:
            sigs = ",".join(r["dialogue_signals"])
            print(f"  [{r['dialogue_score']:.2f}] {r['filename']}")
            print(f"         {r['text'][:140]!r}")
            print(f"         signals: {sigs}")

    if not args.apply:
        print(f"\n(dry-run; pass --apply to move the {len(flagged)} flagged clips to "
              f"{TRASH_DIR})")
        return 0

    if not flagged:
        print("nothing to apply")
        return 0

    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    n_moved = 0
    n_missing = 0
    for r in flagged:
        src = RANKED_DIR / r["filename"]
        if not src.exists():
            n_missing += 1
            continue
        dst = TRASH_DIR / r["filename"]
        shutil.move(str(src), str(dst))
        with MANIFEST_DELETE.open("a") as f:
            f.write(json.dumps({
                "clip_id": r["filename"],
                "rank": r.get("rank"),
                "score": r.get("score"),
                "duration": r.get("duration"),
                "transcript": r.get("text", ""),
                "trash_path": str(dst),
                "reason": "auto-detected character dialogue",
                "dialogue_score": r["dialogue_score"],
                "dialogue_signals": r["dialogue_signals"],
            }) + "\n")
        n_moved += 1

    print(f"\nmoved {n_moved} dialogue clips to {TRASH_DIR}")
    print(f"appended {n_moved} records to {MANIFEST_DELETE}")
    if n_missing:
        print(f"({n_missing} clips already gone — likely deleted previously)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
