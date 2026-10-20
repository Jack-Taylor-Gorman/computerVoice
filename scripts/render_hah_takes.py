#!/usr/bin/env python3
"""Render N takes of Dr Talos's "Ha!" interjection using the affluent
'Quite so, quite so' reference clip. Each take comes out slightly
different because F5's diffusion sampling reseeds per call.

After running:
  ls dataset_avers/arimaspian/hah_takes/take_*.wav
  paplay dataset_avers/arimaspian/hah_takes/take_01.wav   (etc.)

When you've picked the takes you want, list them like:
  scripts/apply_hah_takes.py 3 7 11
…and the chosen takes will be cycled into the main render.
"""
from __future__ import annotations

import argparse
import gc
import subprocess
from pathlib import Path

import torch

_orig = torch.load
def _patched(*a, **kw):
    kw.setdefault("weights_only", False)
    return _orig(*a, **kw)
torch.load = _patched
from f5_tts.api import F5TTS


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dataset_avers" / "arimaspian" / "hah_takes"

REF_CLIP = ROOT / "dataset_avers" / "characters_merged" / "dr-talos" / "clip_0010.wav"
REF_TEXT = ("A new day, Baldanders! Still alive! Time to eat and defecate "
            "and make love -- all that! Up now, or we'll miss it.")

# Per-chunk EQ + a 250ms silence pad at the end so we never clip the
# exhale / breath release after the "Ha?". The diffusion model
# sometimes ends the utterance right on the final phoneme; the apad
# guarantees an audible decay.
EQ = ("equalizer=f=120:t=q:w=1.0:g=2.5,"
      "equalizer=f=5000:t=q:w=1.2:g=-4,"
      "equalizer=f=8000:t=q:w=1.2:g=-2,"
      "lowpass=f=11000:width_type=q:width=0.7,"
      "acompressor=threshold=-22dB:ratio=2:attack=20:release=200,"
      "apad=pad_dur=0.25")


def load_f5():
    cp = ROOT / "venv/lib/python3.10/ckpts/avers_dr_talos/model_last.pt"
    vp = ROOT / "venv/lib/python3.10/data/avers_dr_talos_pinyin/vocab.txt"
    return F5TTS(model="F5TTS_v1_Base", ckpt_file=str(cp),
                 vocab_file=str(vp), device="cuda")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20, help="number of takes")
    ap.add_argument("--text", default="Ha!", help="utterance to render")
    ap.add_argument("--speed", type=float, default=0.85)
    ap.add_argument("--trim-tail-ms", type=int, default=0,
                    help="set to 0 to preserve F5's natural exhale tail")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in out_dir.glob("*.wav"):
        p.unlink()

    print(f"Rendering {args.n} takes of {args.text!r}")
    print(f"  ref: {REF_CLIP.name}")
    print(f"  out: {out_dir}")
    print()

    f5 = load_f5()
    for i in range(1, args.n + 1):
        raw = out_dir / f"raw_{i:02d}.wav"
        dst = out_dir / f"take_{i:02d}.wav"
        f5.infer(
            ref_file=str(REF_CLIP), ref_text=REF_TEXT, gen_text=args.text,
            file_wave=str(raw), show_info=lambda *a, **k: None,
            remove_silence=False, speed=args.speed,
        )
        # If trim_tail_ms > 0, chop that much off the END before EQ.
        # Default (0) preserves the full F5 tail so we keep the exhale.
        if args.trim_tail_ms > 0:
            af = (f"areverse,atrim=start={args.trim_tail_ms / 1000.0},"
                  f"asetpts=PTS-STARTPTS,areverse,{EQ}")
        else:
            af = EQ
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
             "-af", af, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
             str(dst)],
            check=True,
        )
        raw.unlink()
        print(f"  ✓ take_{i:02d}.wav", flush=True)

    del f5
    gc.collect()
    torch.cuda.empty_cache()
    print(f"\nDone. Audition with:")
    print(f"  for f in {out_dir}/take_*.wav; do echo \"$f\"; paplay \"$f\"; done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
