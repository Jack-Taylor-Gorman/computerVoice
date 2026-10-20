#!/usr/bin/env python3
"""Assemble an F5-TTS fine-tune bundle from `dataset_avers/manifests/avers.jsonl`.

The audiobook miner writes one manifest line per kept clip in the form:
    {"audio_path": "clips/avers__000001.wav",
     "text": "He had been a torturer, and he remembered every face.",
     "start": ..., "end": ..., "duration": ...}

`scripts/finetune_f5.sh` expects a bundle directory containing:
    metadata.jsonl   one record per clip — keys: audio_path, transcript
    wavs/            actual clip wav files  (or metadata audio_path resolves
                     to the actual file with relative path resolution against
                     the bundle dir)

We mirror the `dataset/datasets/majel_*_run2/` layout so the F5 prepare step
can read it without surprises:
    <bundle>/wavs/<clip_id>.wav
    <bundle>/metadata.jsonl

Usage:
    ./scripts/build_avers_bundle.py [--max-clips N] [--name avers_run1]
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = ROOT / "dataset_avers"
RAW_MANIFEST = SRC_ROOT / "manifests" / "avers.jsonl"
CURATED_MANIFEST = SRC_ROOT / "manifest_curated.jsonl"
CURATED_CLIPS = SRC_ROOT / "clips_curated"
DST_DIR = ROOT / "dataset_avers" / "datasets"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="avers_run1",
                    help="bundle directory name (default: avers_run1)")
    ap.add_argument("--from-curated", action="store_true",
                    help="build from manifest_curated.jsonl (human-approved set, "
                         "with user-edited transcripts and trim windows applied) "
                         "instead of the raw mining manifest")
    ap.add_argument("--max-clips", type=int, default=None,
                    help="cap clip count for a quick training run")
    ap.add_argument("--min-duration", type=float, default=4.0)
    ap.add_argument("--max-duration", type=float, default=18.0)
    args = ap.parse_args()

    if args.from_curated:
        src_manifest = CURATED_MANIFEST
        wav_source_dir = CURATED_CLIPS
        text_key = "transcript"
        wav_key = "out"          # filename inside clips_curated/
    else:
        src_manifest = RAW_MANIFEST
        wav_source_dir = SRC_ROOT     # raw manifest carries audio_path relative to SRC_ROOT
        text_key = "text"
        wav_key = "audio_path"

    if not src_manifest.exists():
        print(f"missing manifest: {src_manifest}", flush=True)
        return 2

    bundle = DST_DIR / args.name
    wavs = bundle / "wavs"
    wavs.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    for line in src_manifest.read_text().splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        d = float(r.get("duration", 0))
        if d < args.min_duration or d > args.max_duration:
            continue
        if not r.get(text_key) or not r.get(wav_key):
            continue
        rows.append(r)

    if args.max_clips:
        rows = rows[: args.max_clips]

    out_meta = bundle / "metadata.jsonl"
    n_copied = 0
    with out_meta.open("w") as f:
        for r in rows:
            if args.from_curated:
                src = wav_source_dir / r[wav_key]
            else:
                src = wav_source_dir / r[wav_key]
            if not src.exists():
                continue
            stem = Path(src.name).stem
            dst = wavs / f"{stem}.wav"
            if not dst.exists():
                shutil.copy2(src, dst)
            transcript = " ".join(r[text_key].split())
            f.write(json.dumps({
                "audio_path": f"wavs/{stem}.wav",
                "transcript": transcript,
                "duration": r.get("duration"),
            }) + "\n")
            n_copied += 1

    summary = bundle / "bundle_summary.txt"
    summary.write_text(
        f"avers F5 bundle\n"
        f"name: {args.name}\n"
        f"built: {datetime.utcnow().isoformat()}Z\n"
        f"source manifest: {src_manifest}\n"
        f"clips: {n_copied}\n"
        f"duration cap: [{args.min_duration}, {args.max_duration}]\n"
    )

    total_minutes = sum(r.get("duration", 0) for r in rows[:n_copied]) / 60
    print(f"bundle: {bundle}")
    print(f"clips:  {n_copied}")
    print(f"hours:  {total_minutes / 60:.2f}")
    print(f"next:   ./scripts/finetune_f5.sh {bundle}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
