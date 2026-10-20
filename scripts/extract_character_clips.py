#!/usr/bin/env python3
"""Cut per-character wav clips from the audiobook using the book-aligned
word timeline and the speaker attribution.

Inputs:
  dataset_avers/characters/word_alignment.jsonl
  dataset_avers/characters/attribution.jsonl
  dataset_avers/source/shadow_of_the_torturer.wav    (or --source)

For every attributed dialogue span we:
  1. Find the first and last word inside the span via book char offsets
  2. Look up their audio (t_start, t_end) from word_alignment
  3. ffmpeg-cut the wav at [t_start - 0.05, t_end + 0.10]
  4. Drop the clip in dataset_avers/characters/<speaker>/clip_<NNNN>.wav

Severian-narrator-tagged spans that have NO inline-narration siblings —
i.e. the span sits in the middle of a Severian narration paragraph — are
ALSO extracted as part of `severian-narrator`. (Future: scan unspoken
prose for additional narrator clips; for now we only export the
attributed-dialogue spans.)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "dataset_avers" / "characters"
DEFAULT_SOURCE = ROOT / "dataset_avers" / "source" / "shadow_of_the_torturer.wav"


def load_alignment(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def load_attributions(path: Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        out.append(json.loads(line))
    return out


def words_in_span(alignment: list[dict], char_start: int, char_end: int
                  ) -> list[dict]:
    """All aligned words whose char_start falls within [char_start, char_end)."""
    # Linear scan — alignment is already sorted by book_idx.
    return [w for w in alignment
            if char_start <= w["char_start"] < char_end]


def cut_clip(src: Path, start: float, end: float, dst: Path) -> bool:
    """ffmpeg-cut src wav to dst with 24 kHz mono PCM."""
    if end <= start:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{max(0, start - 0.05):.3f}",
         "-to", f"{end + 0.10:.3f}",
         "-i", str(src),
         "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
         str(dst)],
    )
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=str(DEFAULT_SOURCE),
                    help=f"audiobook wav (default: {DEFAULT_SOURCE})")
    ap.add_argument("--alignment", default=str(DEFAULT_OUT_DIR / "word_alignment.jsonl"))
    ap.add_argument("--attribution", default=str(DEFAULT_OUT_DIR / "attribution.jsonl"))
    ap.add_argument("--out", default=str(DEFAULT_OUT_DIR),
                    help=f"output dir for per-character buckets "
                         f"(default: {DEFAULT_OUT_DIR})")
    ap.add_argument("--min-duration", type=float, default=1.0,
                    help="skip clips shorter than this (default 1.0 s)")
    ap.add_argument("--max-duration", type=float, default=20.0,
                    help="skip clips longer than this (default 20 s)")
    ap.add_argument("--min-confidence", type=float, default=0.5,
                    help="only export spans with attribution confidence >= this")
    ap.add_argument("--limit", type=int, default=None,
                    help="only export first N spans (smoke test)")
    ap.add_argument("--skip-narrator", action="store_true",
                    help="don't export the severian-narrator bucket (it's huge "
                         "and mostly redundant with the existing curated set)")
    args = ap.parse_args()

    src = Path(args.source)
    align_path = Path(args.alignment)
    attr_path = Path(args.attribution)
    out_dir = Path(args.out)
    for p in (src, align_path, attr_path):
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr)
            return 2

    alignment = load_alignment(align_path)
    attributions = load_attributions(attr_path)
    print(f"alignment words: {len(alignment)}")
    print(f"attribution spans: {len(attributions)}")
    print()

    per_speaker_idx: dict[str, int] = defaultdict(int)
    per_speaker_seconds: dict[str, float] = defaultdict(float)
    per_speaker_skipped: dict[str, int] = defaultdict(int)
    manifests: dict[str, list[dict]] = defaultdict(list)
    n_out = 0
    n_skip_dur = 0
    n_skip_conf = 0
    n_skip_words = 0

    for i, attr in enumerate(attributions):
        if args.limit and i >= args.limit:
            break
        speaker = attr["speaker"]
        if args.skip_narrator and speaker == "severian-narrator":
            per_speaker_skipped[speaker] += 1
            continue
        if attr.get("confidence", 0) < args.min_confidence:
            n_skip_conf += 1
            per_speaker_skipped[speaker] += 1
            continue
        words = words_in_span(alignment, attr["char_start"], attr["char_end"])
        if not words:
            n_skip_words += 1
            continue
        t_start = min(w["t_start"] for w in words if w.get("t_start") is not None)
        t_end = max(w["t_end"] for w in words if w.get("t_end") is not None)
        dur = t_end - t_start
        if dur < args.min_duration or dur > args.max_duration:
            n_skip_dur += 1
            continue
        per_speaker_idx[speaker] += 1
        idx = per_speaker_idx[speaker]
        dst = out_dir / speaker / f"clip_{idx:04d}.wav"
        if cut_clip(src, t_start, t_end, dst):
            per_speaker_seconds[speaker] += dur
            manifests[speaker].append({
                "clip": dst.name,
                "span_idx": attr["span_idx"],
                "char_start": attr["char_start"],
                "char_end": attr["char_end"],
                "t_start": round(t_start, 3),
                "t_end": round(t_end, 3),
                "duration": round(dur, 3),
                "text": attr["text"],
                "confidence": attr.get("confidence", 0),
                "method": attr.get("method", ""),
            })
            n_out += 1
            if n_out % 100 == 0:
                print(f"  cut {n_out} clips...", flush=True)

    # Write per-speaker manifests.
    for speaker, rows in manifests.items():
        man = out_dir / speaker / "manifest.jsonl"
        with man.open("w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    print(f"\n=== exported {n_out} clips ===")
    print(f"  skipped: {n_skip_dur} (duration), {n_skip_conf} (confidence), "
          f"{n_skip_words} (no aligned words)")
    print()
    print("per-speaker totals:")
    for sp, n in sorted(per_speaker_idx.items(), key=lambda x: -x[1]):
        secs = per_speaker_seconds[sp]
        sk = per_speaker_skipped.get(sp, 0)
        print(f"  {n:>4} clips  {secs/60:>5.1f} min  {sp:<25s} "
              f"({sk} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
