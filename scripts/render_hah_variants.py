#!/usr/bin/env python3
"""Render takes with several text variants to force the /æ/ (apple) vowel
on the Hah interjection. F5's pronunciation is driven by orthography; a
final consonant pulls the vowel toward /æ/. We chop the consonant off in
post.

Variants:
  - "Hap!"  → /hæp/ → trim /p/
  - "Hat!"  → /hæt/ → trim /t/
  - "Han!"  → /hæn/ → trim /n/
  - "Ha-ha!" → paired /hæhæ/
"""
from __future__ import annotations

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
OUT.mkdir(parents=True, exist_ok=True)

REF_CLIP = ROOT / "dataset_avers" / "characters_merged" / "dr-talos" / "clip_0010.wav"
REF_TEXT = ("A new day, Baldanders! Still alive! Time to eat and defecate "
            "and make love -- all that! Up now, or we'll miss it.")

EQ = ("equalizer=f=120:t=q:w=1.0:g=2.5,"
      "equalizer=f=5000:t=q:w=1.2:g=-4,"
      "equalizer=f=8000:t=q:w=1.2:g=-2,"
      "lowpass=f=11000:width_type=q:width=0.7,"
      "acompressor=threshold=-22dB:ratio=2:attack=20:release=200,"
      "apad=pad_dur=0.25")


VARIANTS = [
    ("hap",   "Hap!", 5),
    ("hat",   "Hat!", 5),
    ("han",   "Han!", 5),
    ("haha",  "Ha-ha!", 5),
]


def load_f5():
    cp = ROOT / "venv/lib/python3.10/ckpts/avers_dr_talos/model_last.pt"
    vp = ROOT / "venv/lib/python3.10/data/avers_dr_talos_pinyin/vocab.txt"
    return F5TTS(model="F5TTS_v1_Base", ckpt_file=str(cp),
                 vocab_file=str(vp), device="cuda")


def main() -> int:
    # Wipe non-benchmark files
    for p in OUT.glob("take_*.wav"):
        p.unlink()
    for p in OUT.glob("raw_*.wav"):
        p.unlink()

    f5 = load_f5()
    print()
    for variant_id, text, n in VARIANTS:
        print(f"=== variant {variant_id!r} text={text!r} ({n} takes) ===")
        for i in range(1, n + 1):
            raw = OUT / f"raw_{variant_id}_{i:02d}.wav"
            dst = OUT / f"take_{variant_id}_{i:02d}.wav"
            f5.infer(
                ref_file=str(REF_CLIP), ref_text=REF_TEXT, gen_text=text,
                file_wave=str(raw), show_info=lambda *a, **k: None,
                remove_silence=False, speed=0.85,
            )
            # Trim 100ms off the END to drop the final consonant of
            # "Hap!"/"Hat!"/"Han!" so we're left with just /hæ/.
            trim_ms = 200 if variant_id in ("hap", "hat", "han") else 0
            if trim_ms > 0:
                af = (f"areverse,atrim=start={trim_ms / 1000.0},"
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
            print(f"  ✓ {dst.name}", flush=True)

    del f5; gc.collect(); torch.cuda.empty_cache()
    print(f"\nDone. {OUT}/ has takes named take_<variant>_NN.wav")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
