#!/usr/bin/env python3
"""Slice a cleaned book into reviewable ~5000-word pieces at paragraph
boundaries. For each piece, emit:
  piece_NN.cleaned.txt
  piece_NN.attribution.jsonl   (spans filtered + char offsets rebased)
  piece_NN.pacing.jsonl        (same)
  piece_NN.range.json          ({"start": char, "end": char})

Usage:
  scripts/slice_book_into_pieces.py <cleaned.txt> <attribution.jsonl> <pacing.jsonl> <out_dir> [--target-words N]
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path


def find_piece_boundaries(text: str, target_words: int) -> list[tuple[int, int]]:
    """Return list of (start_char, end_char) cutting at paragraph breaks."""
    paragraphs = []
    pos = 0
    for m in re.finditer(r"\n\s*\n", text):
        paragraphs.append((pos, m.end()))
        pos = m.end()
    if pos < len(text):
        paragraphs.append((pos, len(text)))

    pieces = []
    cur_start = 0
    cur_words = 0
    for ps, pe in paragraphs:
        pw = len(text[ps:pe].split())
        if cur_words + pw > target_words and cur_words > 0:
            pieces.append((cur_start, ps))
            cur_start = ps
            cur_words = pw
        else:
            cur_words += pw
    if cur_start < len(text):
        pieces.append((cur_start, len(text)))
    return pieces


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cleaned")
    ap.add_argument("attribution")
    ap.add_argument("pacing")
    ap.add_argument("out_dir")
    ap.add_argument("--target-words", type=int, default=5000)
    args = ap.parse_args()

    text = Path(args.cleaned).read_text(encoding="utf-8", errors="replace")
    attr = [json.loads(l) for l in Path(args.attribution).read_text().splitlines() if l.strip()]
    pacing_path = Path(args.pacing)
    pacing = ([json.loads(l) for l in pacing_path.read_text().splitlines() if l.strip()]
              if pacing_path.exists() and pacing_path.stat().st_size else [])

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pieces = find_piece_boundaries(text, args.target_words)
    print(f"pieces: {len(pieces)}")
    for i, (cs, ce) in enumerate(pieces, 1):
        body = text[cs:ce].strip()
        if not body:
            continue
        # rebase attribution spans inside [cs, ce)
        p_attr = []
        for a in attr:
            if a["char_start"] >= cs and a["char_end"] <= ce:
                a2 = dict(a)
                a2["char_start"] -= cs
                a2["char_end"] -= cs
                p_attr.append(a2)
        # rebase pacing (uses char_start in our schema; keep tolerant)
        p_pacing = []
        for p in pacing:
            ps = p.get("char_start", 0)
            pe = p.get("char_end", ps)
            if ps >= cs and pe <= ce:
                p2 = dict(p)
                p2["char_start"] = ps - cs
                p2["char_end"] = pe - cs
                p_pacing.append(p2)
        # Re-strip leading whitespace; rebased offsets must still be valid.
        # Since we strip the body, shift char offsets by the stripped count.
        leading_strip = len(text[cs:ce]) - len(text[cs:ce].lstrip())
        if leading_strip:
            for a in p_attr:
                a["char_start"] -= leading_strip
                a["char_end"] -= leading_strip
            for p in p_pacing:
                p["char_start"] -= leading_strip
                p["char_end"] -= leading_strip
        stem = f"piece_{i:02d}"
        (out_dir / f"{stem}.cleaned.txt").write_text(body)
        with (out_dir / f"{stem}.attribution.jsonl").open("w") as f:
            for a in p_attr:
                f.write(json.dumps(a) + "\n")
        with (out_dir / f"{stem}.pacing.jsonl").open("w") as f:
            for p in p_pacing:
                f.write(json.dumps(p) + "\n")
        (out_dir / f"{stem}.range.json").write_text(
            json.dumps({"start": cs, "end": ce, "words": len(body.split()),
                        "attribution_spans": len(p_attr)}))
        print(f"  {stem}: {len(body.split()):>5d} words, "
              f"{len(p_attr):>3d} attribs, {len(p_pacing):>3d} pacing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
