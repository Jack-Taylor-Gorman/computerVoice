#!/usr/bin/env python3
"""Substitute one or more pre-rendered "Hah" takes into the main render's
Hah chunks, then re-concat + re-master + re-build the subtitled video.

Usage:
  scripts/apply_hah_takes.py path/to/take_A.wav [path/to/take_B.wav ...]

If multiple takes are given they're cycled in order across the Hah slots
in the plan. Pass one take to use it everywhere.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "dataset_avers" / "arimaspian"
PLAN_PATH = OUT_DIR / "plan.jsonl"
CHUNKS_DIR = OUT_DIR / "chunks"
MASTER_WAV = OUT_DIR / "complete.wav"

# Same master pass as the main renderer.
MASTER_DYNAMICS = "loudnorm=I=-18:TP=-1.5:LRA=11"


def is_hah_chunk(text: str) -> bool:
    """Return True if this chunk's displayed text is just a Hah-style
    interjection. Matches "Hah.", "Hah!", "Ha!", etc."""
    t = text.strip()
    return t.lower().rstrip(".!?") in ("hah", "ha")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("takes", nargs="+",
                    help="one or more take wavs; cycled in order")
    args = ap.parse_args()

    takes = [Path(p) for p in args.takes]
    for p in takes:
        if not p.exists():
            print(f"missing: {p}", file=sys.stderr); return 2

    plan = [json.loads(l) for l in PLAN_PATH.read_text().splitlines() if l.strip()]

    # Find Hah slots in order
    hah_seqs = [r["seq"] for r in plan if is_hah_chunk(r["text"])]
    print(f"Hah slots: {len(hah_seqs)} (seqs {hah_seqs})")
    print(f"Available takes: {len(takes)} → cycling across {len(hah_seqs)} slots")

    # Overwrite chunk wavs
    for i, seq in enumerate(hah_seqs):
        take = takes[i % len(takes)]
        # Each chunk file is chunk_NNNN_<voice>.wav. The plan has voice.
        voice = next(r for r in plan if r["seq"] == seq)["voice"]
        chunk_path = CHUNKS_DIR / f"chunk_{seq:04d}_{voice}.wav"
        # Copy the take in place but resample to the 48k mono PCM the
        # rest of the chunks use (ffmpeg handles all that).
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(take),
             "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
             str(chunk_path)],
            check=True,
        )
        print(f"  seq {seq:>3d}  ← {take.name}", flush=True)

    # Re-concat master
    print("\nRe-concating master...")
    list_file = OUT_DIR / "complete.concat.txt"
    with list_file.open("w") as f:
        for r in plan:
            voice = r["voice"]
            wav = CHUNKS_DIR / f"chunk_{r['seq']:04d}_{voice}.wav"
            if not wav.exists():
                print(f"  missing: {wav}", file=sys.stderr); return 3
            f.write(f"file '{wav.resolve()}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-af", MASTER_DYNAMICS,
         "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1",
         str(MASTER_WAV)],
        check=True,
    )
    sz = MASTER_WAV.stat().st_size / 1024 / 1024
    print(f"  ✓ {MASTER_WAV} ({sz:.1f} MB)")

    # Invalidate cached subtitle word timings so video re-aligns.
    words_cache = OUT_DIR / "complete.words.json"
    if words_cache.exists():
        words_cache.unlink()
        print(f"  invalidated {words_cache.name}")

    print("\nNext step: rebuild the subtitled video with")
    print(f"  WHISPER_MODEL=medium venv/bin/python scripts/make_subtitle_video.py {MASTER_WAV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
