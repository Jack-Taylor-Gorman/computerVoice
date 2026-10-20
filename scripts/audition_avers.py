#!/usr/bin/env python3
"""Audition Avers / Severian F5 finetune checkpoints side-by-side.

Loads each checkpoint into F5-TTS, synthesizes a fixed panel of representative
Severian-voice phrases, drops wavs into /tmp/severian_audition/<ckpt>/<tag>.wav.
You then `paplay` the same tag from each ckpt's directory and pick the one
whose timbre and cadence read closest to Roy Avers.

Run AFTER F5 finetune training finishes (or after killing the training
process) — synthesis needs the GPU, and cohabiting with training will OOM.

Usage:
    ./scripts/audition_avers.py                    # all available ckpts
    ./scripts/audition_avers.py model_400 model_800  # named subset
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Default to the curated retrain (avers_run2). Override with --run.
DEFAULT_RUN = "avers_run2"
REF_AUDIO = ROOT / "dataset_avers" / "clips_curated" / "avers__000001.wav"
REF_TEXT = ("They'd send to Master Gurlow's. But why would the guard leave? "
            "It doesn't matter.")
OUT = Path("/tmp/severian_audition")

# Fixed eval panel — covers the cadence patterns we care about: a single
# clipped acknowledgement, a refusal, a short imperative, a multi-clause
# enumeration, and a heed-and-fact warning.
PHRASES: list[tuple[str, str]] = [
    ("heard",   "I have heard you."),
    ("done",    "It is done."),
    ("await",   "I await your word."),
    ("cannot",  "I cannot."),
    ("paths",   "Three paths lie before me. The first, repair the gate. "
                "The second, depart by the postern. The third, await your word."),
    ("warning", "Mark this. The middleware that guards the gate did not "
                "check for the empty hand."),
]


def audition(ckpt: Path, name: str, vocab_path: Path) -> bool:
    """Synthesize the eval panel through `ckpt`, write to /tmp/severian_audition/<name>/."""
    import torch as _torch
    _orig_torch_load = _torch.load
    def _patched_load(*a, **kw):
        kw.setdefault("weights_only", False)
        return _orig_torch_load(*a, **kw)
    _torch.load = _patched_load

    from f5_tts.api import F5TTS  # type: ignore

    out_dir = OUT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  loading {name}...", flush=True)
    f5 = F5TTS(
        model="F5TTS_v1_Base",
        ckpt_file=str(ckpt),
        vocab_file=str(vocab_path),
        device="cuda",
    )
    for tag, text in PHRASES:
        dst = out_dir / f"{tag}.wav"
        f5.infer(
            ref_file=str(REF_AUDIO),
            ref_text=REF_TEXT,
            gen_text=text,
            file_wave=str(dst),
            show_info=lambda *a, **k: None,
            remove_silence=False,
            speed=0.7,
        )
        print(f"    ✓ {tag:7s} → {dst}")
    return True


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=DEFAULT_RUN,
                    help=f"run name under venv/lib/python3.10/ckpts/ (default: {DEFAULT_RUN})")
    ap.add_argument("ckpts", nargs="*",
                    help="optional explicit checkpoint stems (e.g. model_5500). "
                         "default: every ckpt in the run dir.")
    args = ap.parse_args()

    ckpt_dir = ROOT / "venv" / "lib" / "python3.10" / "ckpts" / args.run
    vocab_path = ROOT / "venv" / "lib" / "python3.10" / "data" / f"{args.run}_pinyin" / "vocab.txt"

    if not ckpt_dir.exists():
        print(f"missing checkpoint dir: {ckpt_dir}", file=sys.stderr)
        return 2
    if not vocab_path.exists():
        print(f"missing vocab: {vocab_path}", file=sys.stderr)
        return 2
    if not REF_AUDIO.exists():
        print(f"missing reference clip: {REF_AUDIO}", file=sys.stderr)
        return 2

    OUT.mkdir(parents=True, exist_ok=True)
    all_ckpts = sorted(ckpt_dir.glob("model_*.pt"))
    if not all_ckpts:
        print(f"no checkpoints in {ckpt_dir}", file=sys.stderr)
        return 2

    if args.ckpts:
        wanted = set(args.ckpts)
        ckpts = [c for c in all_ckpts if c.stem in wanted]
        if not ckpts:
            print(f"none of {sorted(wanted)} found in {ckpt_dir}", file=sys.stderr)
            return 2
    else:
        ckpts = all_ckpts

    print(f"auditioning {len(ckpts)} checkpoints from {args.run}, output dir {OUT}")
    for c in ckpts:
        try:
            audition(c, c.stem, vocab_path)
        except Exception as e:
            print(f"  ✗ {c.stem}: {e}", file=sys.stderr)

    print()
    print("compare side-by-side, e.g.:")
    print(f"  for d in {OUT}/model_*; do echo \"$d\"; paplay \"$d/done.wav\"; done")
    print()
    print("when you've picked one, set the daemon to use it:")
    print(f"  export SEVERIAN_F5_RUN={args.run}")
    print("  export SEVERIAN_F5_CKPT=model_NNN")
    print("  rm dataset_avers/.no_autostart")
    print("  bash run_f5_daemon.sh &")
    return 0


if __name__ == "__main__":
    sys.exit(main())
