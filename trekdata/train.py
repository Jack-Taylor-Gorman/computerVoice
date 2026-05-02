"""F5-TTS fine-tune launcher.

Usage: python -m trekdata.train --dataset datasets/trek_YYYYMMDD_HHMMSS

Validates dataset minimums before launching so VRAM is not wasted on
unviable corpora. F5-TTS is the chosen target (MIT, 15-30 min floor,
8 GB VRAM fine-tune with gradient checkpointing).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


MIN_HOURS = 0.25
TARGET_HOURS = 1.0
MIN_PHONEMES = 35
MAJEL_CHECKPOINT_DIR = Path(__file__).resolve().parent.parent / "Majel" / "f5tts"


def validate(dataset: Path) -> tuple[bool, list[str]]:
    issues: list[str] = []
    card = dataset / "export_manifest.json"
    if not card.exists():
        return False, [f"missing {card}"]
    meta = json.loads(card.read_text())
    hours = meta.get("hours", 0.0)
    if hours < MIN_HOURS:
        issues.append(f"only {hours:.2f}h available; minimum {MIN_HOURS}h")
    elif hours < TARGET_HOURS:
        issues.append(f"only {hours:.2f}h available; target {TARGET_HOURS}h for production quality")

    cov = dataset / "phoneme_coverage.json"
    if cov.exists():
        phonemes = json.loads(cov.read_text())
        if len(phonemes) < MIN_PHONEMES:
            issues.append(
                f"{len(phonemes)} IPA symbols covered; minimum {MIN_PHONEMES}. "
                f"Generate synthetic clips via speak.py to fill gaps."
            )
    return not any(s.startswith("only") and "minimum" in s for s in issues), issues


def launch(dataset: Path, epochs: int, batch: int, grad_ckpt: bool, resume: str | None) -> int:
    MAJEL_CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "f5-tts_finetune-cli",
        "--dataset_path", str(dataset),
        "--metadata_file", str(dataset / "metadata.csv"),
        "--output_dir", str(MAJEL_CHECKPOINT_DIR),
        "--epochs", str(epochs),
        "--batch_size", str(batch),
        "--sample_rate", "24000",
    ]
    if grad_ckpt:
        cmd += ["--gradient_checkpointing"]
    if resume:
        cmd += ["--resume_from_checkpoint", resume]
    print("launching:", " ".join(cmd))
    return subprocess.call(cmd)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--no-grad-ckpt", action="store_true")
    ap.add_argument("--resume", type=str, default=None)
    ap.add_argument("--force", action="store_true", help="skip validation gates")
    args = ap.parse_args()

    ok, issues = validate(args.dataset)
    for s in issues:
        print(f"  - {s}")
    if not ok and not args.force:
        print("validation failed. pass --force to proceed anyway.")
        return 2
    return launch(args.dataset, args.epochs, args.batch, not args.no_grad_ckpt, args.resume)


if __name__ == "__main__":
    sys.exit(main())
