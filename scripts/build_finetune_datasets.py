#!/usr/bin/env python3
"""Assemble two F5-TTS-compatible fine-tune datasets from manifest_curated.jsonl:

  dataset/datasets/majel_all_<ts>/    every accepted clip   (incl. flagged)
  dataset/datasets/majel_clean_<ts>/  accepted clips ONLY when not flagged

Each bundle is the LJSpeech-flavored layout the project's trekdata.train
launcher expects:

    wavs/<clip_id>.wav                 24 kHz mono 16-bit
    metadata.csv                       <clip_id>|<transcript>|<normalised>
    metadata.jsonl                     one full record per line
    splits/{train,val,test}.txt        hashed 90/5/5 split
    phoneme_coverage.json              best-effort IPA inventory
    dataset_card.md
    export_manifest.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACCEPT = ROOT / "dataset" / "manifest_curated.jsonl"
CURATED_WAVS = ROOT / "dataset" / "clips_curated"
DATASETS_DIR = ROOT / "dataset" / "datasets"

TARGET_SR = 24_000
TRAIN_FRAC = 0.90
VAL_FRAC = 0.05  # rest → test

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s']")


def normalize_text(text: str) -> str:
    t = _NORMALIZE_RE.sub("", text.lower())
    return re.sub(r"\s+", " ", t).strip()


def clip_id_from_filename(name: str) -> str:
    return Path(name).stem


def split_bucket(clip_id: str) -> str:
    h = int(hashlib.sha1(clip_id.encode()).hexdigest(), 16) / (1 << 160)
    if h < TRAIN_FRAC:
        return "train"
    if h < TRAIN_FRAC + VAL_FRAC:
        return "val"
    return "test"


def to_ipa(text: str) -> str:
    """Best-effort IPA — return '' if phonemizer/espeak isn't installed."""
    try:
        from phonemizer import phonemize  # type: ignore
        return phonemize(text, language="en-us", backend="espeak",
                         strip=True, preserve_punctuation=False)
    except Exception:
        return ""


def load_accepted() -> list[dict]:
    rows: list[dict] = []
    if not ACCEPT.exists():
        return rows
    for line in ACCEPT.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    # Manifest is append-only, so:
    #   1. Dedupe by clip_id (latest wins for re-decisions).
    #   2. Dedupe by normalized transcript text — the same line mined from
    #      two episodes is a transcript leak between train/val. Keep the
    #      LONGEST clip per text (more context for the model) and prefer
    #      unflagged when durations tie.
    by_id: dict[str, dict] = {}
    for r in rows:
        by_id[r["clip_id"]] = r
    by_text: dict[str, dict] = {}
    for r in by_id.values():
        key = normalize_text(r.get("transcript", ""))
        if not key:
            continue
        existing = by_text.get(key)
        if existing is None:
            by_text[key] = r
            continue
        # Tiebreakers: prefer unflagged, then longer duration.
        if (not r.get("flagged")) and existing.get("flagged"):
            by_text[key] = r
        elif r.get("flagged") and not existing.get("flagged"):
            pass
        elif float(r.get("duration", 0)) > float(existing.get("duration", 0)):
            by_text[key] = r
    return list(by_text.values())


def resample_to(src: Path, dst: Path, sr: int = TARGET_SR) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )


def build_one(out_dir: Path, rows: list[dict], label: str) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "wavs").mkdir(exist_ok=True)
    (out_dir / "splits").mkdir(exist_ok=True)

    csv_lines: list[str] = []
    jsonl_lines: list[str] = []
    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    coverage: set[str] = set()
    total_dur = 0.0
    n_kept = 0

    for r in rows:
        src_name = r.get("out") or r.get("src") or r.get("clip_id")
        src = CURATED_WAVS / src_name
        if not src.exists():
            print(f"  ! missing wav: {src.name}, skipping")
            continue
        clip_id = clip_id_from_filename(src_name)
        transcript = (r.get("transcript") or "").strip()
        if not transcript:
            print(f"  ! empty transcript for {clip_id}, skipping")
            continue

        dst = out_dir / "wavs" / f"{clip_id}.wav"
        resample_to(src, dst)

        norm = normalize_text(transcript)
        ipa = to_ipa(transcript)
        coverage.update(ch for ch in ipa if not ch.isspace())
        dur = float(r.get("duration", 0.0))
        total_dur += dur

        bucket = split_bucket(clip_id)
        splits[bucket].append(clip_id)

        csv_lines.append(f"{clip_id}|{transcript}|{norm}")
        jsonl_lines.append(json.dumps({
            "clip_id": clip_id,
            "audio_path": f"wavs/{clip_id}.wav",
            "transcript": transcript,
            "transcript_normalized": norm,
            "duration_sec": round(dur, 3),
            "speaker_id": "majel_barrett",
            "phonemes": ipa,
            "flagged": bool(r.get("flagged")),
            "flag_note": r.get("flag_note", ""),
            "src": r.get("src"),
        }, ensure_ascii=False))
        n_kept += 1

    (out_dir / "metadata.csv").write_text("\n".join(csv_lines) + "\n")
    (out_dir / "metadata.jsonl").write_text("\n".join(jsonl_lines) + "\n")
    for name, ids in splits.items():
        (out_dir / "splits" / f"{name}.txt").write_text(
            "\n".join(ids) + ("\n" if ids else "")
        )
    (out_dir / "phoneme_coverage.json").write_text(
        json.dumps(sorted(coverage), ensure_ascii=False, indent=2)
    )

    hours = total_dur / 3600.0
    flagged_kept = sum(1 for r in rows if r.get("flagged"))
    (out_dir / "export_manifest.json").write_text(json.dumps({
        "run_id": out_dir.name,
        "label": label,
        "created_at": datetime.utcnow().isoformat(),
        "schema_version": 1,
        "clip_count": n_kept,
        "flagged_count": flagged_kept,
        "duration_seconds": round(total_dur, 3),
        "hours": round(hours, 4),
        "sample_rate": TARGET_SR,
        "train_frac": TRAIN_FRAC,
        "val_frac": VAL_FRAC,
    }, indent=2))
    (out_dir / "dataset_card.md").write_text(
        f"# Majel Fine-Tune Dataset — {label}\n\n"
        f"- Run id: `{out_dir.name}`\n"
        f"- Clips: {n_kept}\n"
        f"- Total duration: {total_dur:.1f}s ({hours:.3f}h)\n"
        f"- Sample rate: {TARGET_SR} Hz mono 16-bit\n"
        f"- Phoneme coverage: {len(coverage)} IPA symbols\n"
        f"- Flagged clips included: {flagged_kept}\n"
        f"- Splits: train={len(splits['train'])}  val={len(splits['val'])}  test={len(splits['test'])}\n"
        f"- Source: dataset/manifest_curated.jsonl\n"
    )
    return {
        "path": str(out_dir),
        "label": label,
        "clips": n_kept,
        "flagged": flagged_kept,
        "duration_s": total_dur,
        "splits": {k: len(v) for k, v in splits.items()},
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()

    rows = load_accepted()
    if not rows:
        sys.stderr.write(f"no accepted clips in {ACCEPT}\n")
        return 2
    flagged = [r for r in rows if r.get("flagged")]
    unflagged = [r for r in rows if not r.get("flagged")]
    print(f"accepted={len(rows)}  flagged={len(flagged)}  unflagged={len(unflagged)}")

    DATASETS_DIR.mkdir(parents=True, exist_ok=True)

    all_dir = DATASETS_DIR / f"majel_all_{args.tag}"
    clean_dir = DATASETS_DIR / f"majel_clean_{args.tag}"

    print(f"\nbuilding ALL  → {all_dir.name}")
    summ_all = build_one(all_dir, rows, label="all (incl. flagged)")
    print(f"  kept {summ_all['clips']} clips ({summ_all['duration_s']:.1f}s, {summ_all['flagged']} flagged)")
    print(f"  splits: {summ_all['splits']}")

    print(f"\nbuilding CLEAN → {clean_dir.name}")
    summ_clean = build_one(clean_dir, unflagged, label="clean (no flagged)")
    print(f"  kept {summ_clean['clips']} clips ({summ_clean['duration_s']:.1f}s)")
    print(f"  splits: {summ_clean['splits']}")

    print("\n=== READY ===")
    print(f"All:   {summ_all['path']}")
    print(f"Clean: {summ_clean['path']}")
    print()
    print("To launch the two fine-tunes (one at a time — single GPU):")
    print(f"  ./venv/bin/python -m trekdata.train --dataset {all_dir}")
    print(f"  ./venv/bin/python -m trekdata.train --dataset {clean_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
