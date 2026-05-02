#!/usr/bin/env python3
"""Re-transcribe every clip in dataset/clips_ranked/ end-to-end with Whisper
and write the full transcripts to dataset/transcript_overrides.json.

Why this exists: export_top_clips.py truncates the transcript to ~40 chars
when building the filename slug, so the curator UI sees a chopped line.
This script rebuilds the full transcript for the entire audio of each clip,
which the curator picks up automatically (overrides win over the slug).

Usage:
    ./venv/bin/python scripts/retranscribe_clips.py
    ./venv/bin/python scripts/retranscribe_clips.py --model medium.en --force
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RANKED_DIR = ROOT / "dataset" / "clips_ranked"
OVERRIDES = ROOT / "dataset" / "transcript_overrides.json"


def load_overrides() -> dict[str, str]:
    if OVERRIDES.exists():
        try:
            return json.loads(OVERRIDES.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_overrides(d: dict[str, str]) -> None:
    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    tmp = OVERRIDES.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True))
    tmp.replace(OVERRIDES)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="medium.en",
                    help="Whisper model size (default: medium.en).")
    ap.add_argument("--force", action="store_true",
                    help="Re-transcribe clips that already have an override.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Stop after N clips (0 = no limit).")
    args = ap.parse_args()

    if not RANKED_DIR.exists():
        sys.stderr.write(f"Missing {RANKED_DIR}\n")
        return 2

    clips = sorted(RANKED_DIR.glob("*.wav"))
    if not clips:
        sys.stderr.write(f"No .wav files in {RANKED_DIR}\n")
        return 2

    overrides = load_overrides()
    todo = [p for p in clips if args.force or p.name not in overrides]
    if args.limit:
        todo = todo[: args.limit]

    print(f"clips total={len(clips)}  todo={len(todo)}  model={args.model}")
    if not todo:
        print("nothing to do (use --force to overwrite).")
        return 0

    import torch  # type: ignore
    import whisper  # type: ignore

    device = "cuda" if torch.cuda.is_available() else "cpu"
    fp16 = device == "cuda"
    print(f"loading whisper model={args.model} device={device} fp16={fp16}")
    t0 = time.monotonic()
    model = whisper.load_model(args.model, device=device)
    print(f"loaded in {time.monotonic() - t0:.1f}s")

    saved = 0
    for i, p in enumerate(todo, 1):
        try:
            # condition_on_previous_text=False: each clip is independent.
            # no_speech_threshold raised slightly so very short utterances
            # still come through. Default chunking covers the full audio.
            result = model.transcribe(
                str(p),
                language="en",
                fp16=fp16,
                verbose=False,
                condition_on_previous_text=False,
                temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
                no_speech_threshold=0.4,
                logprob_threshold=-1.0,
            )
            text = (result.get("text") or "").strip()
            text = " ".join(text.split())
        except Exception as e:
            print(f"[{i}/{len(todo)}] {p.name} ERROR: {e}")
            continue

        if not text:
            print(f"[{i}/{len(todo)}] {p.name} (empty)")
            continue

        overrides[p.name] = text
        print(f"[{i}/{len(todo)}] {p.name}\n    → {text}")
        saved += 1
        # Persist every 5 clips so a crash doesn't lose work.
        if saved % 5 == 0:
            save_overrides(overrides)

    save_overrides(overrides)
    print(f"\nwrote {saved} new transcripts → {OVERRIDES}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
