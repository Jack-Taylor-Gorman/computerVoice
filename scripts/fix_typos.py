#!/usr/bin/env python3
"""Apply same-LENGTH OCR typo fixes to a cleaned.txt file in place.

Length-preserving substitutions don't change char offsets, so any
existing attribution.jsonl spans remain valid after this runs.

Usage:
  scripts/fix_typos.py <cleaned.txt> [<cleaned.txt> ...]
"""
from __future__ import annotations
import sys
from pathlib import Path

# (pattern, replacement) — both MUST be the same length.
# Use word-boundary aware replacements where possible.
SAME_LENGTH_FIXES = [
    ("Biood", "Blood"),       # repeated OCR error
    ("biood", "blood"),
    ("Wotff", "Wolfe"),
    ("Wotfe", "Wolfe"),
    ("THE LONG SON", "THE LONG SUN"),
    ("needier", "needler"),   # "needler" = laser pistol in the book
    ("Needier", "Needler"),
    ("manteion", "manteion"),  # already correct, no-op (placeholder)
    ("manted", "manted"),     # no-op
    ("Frodvad", "Frodvad"),   # placeholder for other typos as found
    # mantian → manteion in some places — different lengths, skip
]


def fix_file(p: Path) -> int:
    text = p.read_text(encoding="utf-8")
    orig_len = len(text)
    count = 0
    for bad, good in SAME_LENGTH_FIXES:
        assert len(bad) == len(good), f"length mismatch: {bad!r} vs {good!r}"
        if bad == good:
            continue
        new = text.replace(bad, good)
        if new != text:
            n = (len(text) - len(new.replace(good, ""))) // len(good) if len(good) else 0
            occ = text.count(bad)
            print(f"  {p.name}: {bad!r} → {good!r}  ({occ} occurrences)")
            count += occ
            text = new
    if len(text) != orig_len:
        print(f"  WARN: length changed {orig_len} → {len(text)} — DID NOT WRITE")
        return 0
    if count:
        p.write_text(text, encoding="utf-8")
    return count


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fix_typos.py <cleaned.txt> [...]", file=sys.stderr)
        return 2
    total = 0
    for arg in sys.argv[1:]:
        total += fix_file(Path(arg))
    print(f"total fixes: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
