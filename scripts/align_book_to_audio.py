#!/usr/bin/env python3
"""Force-align the canonical book text to the WhisperX word-level audio
timestamps. Output is one record per book word giving its audio time span.

WhisperX has ~95% accuracy on this audiobook; the book text is ground
truth. We use difflib.SequenceMatcher on lowercase alpha-only tokens to
find matching blocks, then interpolate timings across unmatched runs.

Input:
  dataset_avers/cache/asr.json           — WhisperX cached output
  <book.txt>                              — canonical text

Output:
  dataset_avers/characters/word_alignment.jsonl
    {book_idx, word, char_start, char_end, t_start, t_end, matched: bool}

The (char_start, char_end) is the offset into the cleaned book text (after
applying read_aloud.clean_text). The (t_start, t_end) is seconds into the
source wav. Unmatched words have t_start/t_end interpolated from
surrounding matched anchors; their `matched` flag is False.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASR_CACHE = ROOT / "dataset_avers" / "cache" / "asr.json"
OUT_DIR = ROOT / "dataset_avers" / "characters"

WORD_RE = re.compile(r"[A-Za-z']+")


def tokenize(text: str) -> list[tuple[str, int, int]]:
    """Return [(normalised_word, char_start, char_end), ...] over the text."""
    out: list[tuple[str, int, int]] = []
    for m in WORD_RE.finditer(text):
        w = m.group(0).lower().strip("'")
        if w:
            out.append((w, m.start(), m.end()))
    return out


def load_whisperx_words(asr: dict) -> list[tuple[str, float, float, float]]:
    """Return [(normalised_word, t_start, t_end, score), ...]."""
    out: list[tuple[str, float, float, float]] = []
    for ws in asr.get("word_segments", []):
        w = ws.get("word", "")
        if not w:
            continue
        m = WORD_RE.search(w)
        if not m:
            continue
        norm = m.group(0).lower().strip("'")
        if not norm:
            continue
        # Some words missed alignment (WhisperX sometimes drops timing);
        # skip if no start/end — they'll be unmatched on the WX side.
        ts = ws.get("start")
        te = ws.get("end")
        if ts is None or te is None:
            continue
        out.append((norm, float(ts), float(te), float(ws.get("score") or 0.5)))
    return out


def align_blocks(book_tokens: list[str], wx_tokens: list[str]) -> list[tuple[int, int, int]]:
    """SequenceMatcher matching blocks: list of (book_idx, wx_idx, size)."""
    sm = difflib.SequenceMatcher(a=book_tokens, b=wx_tokens, autojunk=False)
    blocks = sm.get_matching_blocks()
    return [(b.a, b.b, b.size) for b in blocks if b.size > 0]


def build_book_time_map(
    book_words: list[tuple[str, int, int]],
    wx_words: list[tuple[str, float, float, float]],
) -> list[dict]:
    """For every book word emit a record with its audio time span."""
    book_toks = [w[0] for w in book_words]
    wx_toks = [w[0] for w in wx_words]

    print(f"  book tokens: {len(book_toks)}", flush=True)
    print(f"  asr  tokens: {len(wx_toks)}", flush=True)
    print(f"  running difflib SequenceMatcher (may take a few minutes)...", flush=True)

    t0 = time.monotonic()
    blocks = align_blocks(book_toks, wx_toks)
    print(f"  alignment: {len(blocks)} matching blocks "
          f"({time.monotonic()-t0:.1f}s)", flush=True)

    # For each book word, find the WX word it aligns to (if any).
    book_to_wx: list[int | None] = [None] * len(book_toks)
    matched_count = 0
    for bi, wi, sz in blocks:
        for k in range(sz):
            book_to_wx[bi + k] = wi + k
            matched_count += 1
    print(f"  matched {matched_count} of {len(book_toks)} book tokens "
          f"({matched_count/max(1,len(book_toks)):.1%})", flush=True)

    # Now produce the per-book-word records. Unmatched words get times
    # interpolated linearly between the surrounding matched anchors.
    records: list[dict] = []
    for bi, (norm, cs, ce) in enumerate(book_words):
        wi = book_to_wx[bi]
        if wi is not None:
            _, ts, te, score = wx_words[wi]
            records.append({
                "book_idx": bi,
                "word": norm,
                "char_start": cs,
                "char_end": ce,
                "t_start": ts,
                "t_end": te,
                "matched": True,
                "score": score,
            })
        else:
            records.append({
                "book_idx": bi,
                "word": norm,
                "char_start": cs,
                "char_end": ce,
                "t_start": None,
                "t_end": None,
                "matched": False,
                "score": 0.0,
            })

    # Interpolate t_start/t_end across unmatched runs.
    last_matched_t = None
    last_matched_i = None
    for i, rec in enumerate(records):
        if rec["matched"]:
            if last_matched_i is not None and i > last_matched_i + 1:
                # Fill the gap.
                ts0 = records[last_matched_i]["t_end"]
                ts1 = rec["t_start"]
                gap = i - last_matched_i
                for k in range(1, gap):
                    j = last_matched_i + k
                    frac = k / gap
                    records[j]["t_start"] = ts0 + (ts1 - ts0) * frac
                    records[j]["t_end"] = ts0 + (ts1 - ts0) * frac
            last_matched_i = i
    # Tail: any unmatched words after the last matched one get the last_matched_t.
    if last_matched_i is not None:
        last_t = records[last_matched_i]["t_end"]
        for j in range(last_matched_i + 1, len(records)):
            records[j]["t_start"] = last_t
            records[j]["t_end"] = last_t
    # Head: any unmatched words before the first matched one get t=0.
    for j, rec in enumerate(records):
        if rec["matched"]:
            break
        rec["t_start"] = 0.0
        rec["t_end"] = 0.0

    return records


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("book", help="path to the canonical book .txt file")
    ap.add_argument("--asr", default=str(ASR_CACHE),
                    help=f"WhisperX cache (default: {ASR_CACHE})")
    ap.add_argument("--out", default=None,
                    help="output JSONL (default: dataset_avers/characters/word_alignment.jsonl)")
    args = ap.parse_args()

    book_path = Path(args.book)
    if not book_path.exists():
        print(f"missing book: {book_path}", file=sys.stderr)
        return 2
    asr_path = Path(args.asr)
    if not asr_path.exists():
        print(f"missing asr cache: {asr_path}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out) if args.out else (OUT_DIR / "word_alignment.jsonl")

    print(f"book: {book_path}")
    print(f"asr:  {asr_path}")
    print(f"out:  {out_path}")
    print()

    book_text = book_path.read_text(encoding="utf-8", errors="replace")
    book_words = tokenize(book_text)
    asr = json.loads(asr_path.read_text())
    wx_words = load_whisperx_words(asr)

    records = build_book_time_map(book_words, wx_words)

    with out_path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    print(f"\nwrote {len(records)} records → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
