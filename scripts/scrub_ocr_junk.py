#!/usr/bin/env python3
"""Remove OCR boilerplate (page headers/footers) from a cleaned book and
rebase the existing attribution.jsonl + pacing.jsonl offsets so we don't
have to re-run the LLM attribution pass.

The renderer was reading aloud junk like 'NlGHTSJDE', '74 Gene Wotff',
'^*' because they slipped through the cleaner. QC then flagged them as
'bad chunks' because Whisper couldn't transcribe them — wasted retries
and shipped audio reading page headers.

Usage:
  scripts/scrub_ocr_junk.py <cleaned.txt> <attribution.jsonl> <pacing.jsonl> <out_stem>
emits:
  <out_stem>.cleaned.txt
  <out_stem>.attribution.jsonl
  <out_stem>.pacing.jsonl
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path


# Patterns of stretches we delete entirely (header/footer boilerplate).
# Each pattern is applied to the cleaned text; matches are EXCISED and
# the offset map is rebuilt accordingly.
JUNK_PATTERNS = [
    # mangled NIGHTSIDE page header
    r"N[lI1]GHTSJ?D?E?",
    # "74 Gene Wotff", "124 Gene Wotfe", etc.
    r"\d{1,3}\s+Gene\s+Wo[lt]f?f?e?",
    r"Gene\s+Wo[lt]f?f?e?\s+\d{1,3}",
    r"\bGENE\s+WOLFE\b",
    # series banner
    r"\bTHE\s+BOOK\s+OF\s+THE\s+LONG\s+SUN\b",
    # mangled chapter markers (only the OCR-broken variants — keep
    # "Chapter 2", "Chapter 12", etc.; strip "Chapter j", "Chapter ll")
    r"Chapter\s+(?:l{2,3}|j|J|[a-h]|[k-z])\b",
    # caret-star and similar OCR debris
    r"\^\*+",
    # standalone page numbers on their own line (1-3 digits)
    r"(?m)^\s*\d{1,3}\s*$",
]


def build_excision_ranges(text: str) -> list[tuple[int, int]]:
    """Find all (start, end) ranges to delete."""
    ranges = []
    for pat in JUNK_PATTERNS:
        for m in re.finditer(pat, text):
            ranges.append((m.start(), m.end()))
    # sort + merge overlaps
    if not ranges:
        return []
    ranges.sort()
    merged = [ranges[0]]
    for s, e in ranges[1:]:
        ms, me = merged[-1]
        if s <= me:
            merged[-1] = (ms, max(me, e))
        else:
            merged.append((s, e))
    return merged


def apply_excisions(text: str, ranges: list[tuple[int, int]]) -> tuple[str, list[int]]:
    """Return (scrubbed_text, offset_map).
    offset_map[i] = new char position for old i, or -1 if removed."""
    n = len(text)
    keep = [True] * n
    for s, e in ranges:
        for i in range(s, min(e, n)):
            keep[i] = False
    out_chars = []
    offset_map = [-1] * n
    new_pos = 0
    for i, ch in enumerate(text):
        if keep[i]:
            offset_map[i] = new_pos
            out_chars.append(ch)
            new_pos += 1
    scrubbed = "".join(out_chars)
    # Collapse extra whitespace gaps from the deletions
    scrubbed2, offset_map2 = _collapse_whitespace(scrubbed, offset_map, n)
    return scrubbed2, offset_map2


def _collapse_whitespace(text: str, offset_map: list[int], original_len: int) -> tuple[str, list[int]]:
    """After excision we often leave stretches like ' \\n  \\n\\n  '. Collapse
    runs of 3+ blank lines into 2, and remove leading whitespace on a now-
    empty line."""
    # Simple: collapse 3+ newlines → 2 newlines
    out_chars = []
    new_to_old = []  # new_to_old[i] = original char position
    # First build new_to_old from offset_map
    for old_i, new_i in enumerate(offset_map):
        if new_i >= 0:
            new_to_old.append(old_i)
    # Now collapse \n{3,} into \n\n
    i = 0
    final_chars = []
    final_new_to_old = []
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            # count run
            j = i
            while j < len(text) and text[j] == "\n":
                j += 1
            keep = min(j - i, 2)
            for k in range(keep):
                final_chars.append("\n")
                final_new_to_old.append(new_to_old[i + k])
            i = j
        else:
            final_chars.append(ch)
            final_new_to_old.append(new_to_old[i])
            i += 1
    new_text = "".join(final_chars)
    # Rebuild offset_map (old → new collapsed pos)
    new_offset_map = [-1] * original_len
    for new_pos, old_pos in enumerate(final_new_to_old):
        new_offset_map[old_pos] = new_pos
    return new_text, new_offset_map


def rebase_span(old_start: int, old_end: int, offset_map: list[int]) -> tuple[int, int] | None:
    """Map a [old_start, old_end) span to the scrubbed coordinates.
    Returns None if the span was entirely inside an excised region."""
    n = len(offset_map)
    # find first surviving char ≥ old_start
    s = None
    for i in range(max(0, old_start), min(n, old_end)):
        if offset_map[i] >= 0:
            s = offset_map[i]
            break
    if s is None:
        return None
    # find last surviving char < old_end
    e = None
    for i in range(min(n, old_end) - 1, max(-1, old_start - 1), -1):
        if offset_map[i] >= 0:
            e = offset_map[i] + 1
            break
    if e is None:
        return None
    return (s, e)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cleaned")
    ap.add_argument("attribution")
    ap.add_argument("pacing")
    ap.add_argument("out_stem")
    args = ap.parse_args()

    cleaned = Path(args.cleaned).read_text(encoding="utf-8")
    attribution = [json.loads(l) for l in Path(args.attribution).read_text().splitlines() if l.strip()]
    pacing_text = Path(args.pacing).read_text() if Path(args.pacing).exists() else ""
    pacing = [json.loads(l) for l in pacing_text.splitlines() if l.strip()]

    ranges = build_excision_ranges(cleaned)
    total_removed = sum(e - s for s, e in ranges)
    print(f"junk ranges: {len(ranges)} totaling {total_removed} chars "
          f"({total_removed / max(1,len(cleaned)) * 100:.2f}%)")
    sample = [(s, e, cleaned[s:e][:60].replace("\n", "⏎")) for s, e in ranges[:5]]
    for s, e, snip in sample:
        print(f"  @{s}..{e}  {snip!r}")

    scrubbed, offset_map = apply_excisions(cleaned, ranges)
    print(f"text: {len(cleaned):,} → {len(scrubbed):,} chars")

    new_attribution = []
    dropped = 0
    for a in attribution:
        m = rebase_span(a["char_start"], a["char_end"], offset_map)
        if m is None:
            dropped += 1; continue
        a2 = dict(a); a2["char_start"], a2["char_end"] = m
        new_attribution.append(a2)
    print(f"attribution: {len(attribution)} → {len(new_attribution)} (dropped {dropped})")

    new_pacing = []
    for p in pacing:
        cs = p.get("char_start", 0); ce = p.get("char_end", cs)
        m = rebase_span(cs, ce, offset_map)
        if m is None:
            continue
        p2 = dict(p); p2["char_start"], p2["char_end"] = m
        new_pacing.append(p2)

    out_stem = Path(args.out_stem)
    out_stem.parent.mkdir(parents=True, exist_ok=True)
    (out_stem.with_name(out_stem.name + ".cleaned.txt")).write_text(scrubbed)
    with (out_stem.with_name(out_stem.name + ".attribution.jsonl")).open("w") as f:
        for a in new_attribution:
            f.write(json.dumps(a) + "\n")
    with (out_stem.with_name(out_stem.name + ".pacing.jsonl")).open("w") as f:
        for p in new_pacing:
            f.write(json.dumps(p) + "\n")
    print(f"wrote: {out_stem}.cleaned.txt / .attribution.jsonl / .pacing.jsonl")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
