#!/usr/bin/env python3
"""Test renderer: re-synth chunks 87-104 of the Arimaspian plan WITHOUT
per-chunk loudnorm or compressor. Concat them, then apply ONE master
loudnorm at the very end. This should remove the dip-at-seam bug.

Compare side by side with the same range from the existing complete.wav.
"""
import json
import subprocess
import gc
from pathlib import Path
import torch

_orig = torch.load
def _p(*a, **kw):
    kw.setdefault("weights_only", False); return _orig(*a, **kw)
torch.load = _p
from f5_tts.api import F5TTS

ROOT = Path(".")
PLAN = ROOT / "dataset_avers/arimaspian/plan.jsonl"
OUT = ROOT / "dataset_avers/arimaspian/test_concat"
OUT.mkdir(parents=True, exist_ok=True)
for p in OUT.glob("*"): p.unlink()

# Range: chunks 87 through 104 inclusive (back-and-forth section)
SEQ_LO = 87
SEQ_HI = 104

# EQ chain WITHOUT loudnorm / compressor — only spectral shaping
# (those don't introduce gain drift over time).
EQ_NO_GAIN = (
    "equalizer=f=120:t=q:w=1.0:g=2.5,"
    "equalizer=f=5000:t=q:w=1.2:g=-4,"
    "equalizer=f=8000:t=q:w=1.2:g=-2,"
    "lowpass=f=11000:width_type=q:width=0.7"
)

# Master pass: ONE loudnorm + ONE compressor over the whole concat output
MASTER_DYNAMICS = (
    "acompressor=threshold=-22dB:ratio=2:attack=20:release=200,"
    "loudnorm=I=-18:TP=-1.5:LRA=11"
)


def char_ref(char):
    man = ROOT / f"dataset_avers/characters_merged/{char}/manifest.jsonl"
    def calm_score(r):
        text = r.get("text", ""); dur = r.get("duration") or 0
        if dur < 5.0 or dur > 9.0 or len(text) < 50: return -1
        return text.count(".") - 2 * text.count("!") - text.count("?")
    cands = []
    for line in man.read_text().splitlines():
        r = json.loads(line)
        if calm_score(r) >= 0: cands.append((calm_score(r), r))
    cands.sort(key=lambda x: -x[0])
    _, best = cands[0]
    return (ROOT / f"dataset_avers/characters_merged/{char}/{best['clip']}",
            " ".join(best["text"].split()))


VOICE_REFS = {
    "narrator":  (ROOT / "dataset_avers/clips_curated/avers__000001.wav",
                  "They'd send to Master Gurlow's. But why would the guard leave? It doesn't matter."),
    "dr_talos":  char_ref("dr-talos"),
}


def load_f5(run, ckpt):
    cp = ROOT / f"venv/lib/python3.10/ckpts/{run}/{ckpt}.pt"
    vp = ROOT / f"venv/lib/python3.10/data/{run}_pinyin/vocab.txt"
    return F5TTS(model="F5TTS_v1_Base", ckpt_file=str(cp),
                 vocab_file=str(vp), device="cuda")


def synth_chunk(f5, ref, ref_text, gen_text, dst, speed=0.7):
    raw = OUT / f"raw_{dst.stem}.wav"
    f5.infer(ref_file=str(ref), ref_text=ref_text, gen_text=gen_text,
             file_wave=str(raw), show_info=lambda *a, **k: None,
             remove_silence=False, speed=speed)
    # Apply only spectral EQ — no compressor, no loudnorm
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw),
                    "-af", EQ_NO_GAIN, "-ar", "48000", "-ac", "1",
                    "-c:a", "pcm_s16le", str(dst)], check=True)
    raw.unlink()


# Load plan, filter to seq range
plan_full = [json.loads(l) for l in PLAN.read_text().splitlines() if l.strip()]
plan = [r for r in plan_full if SEQ_LO <= r["seq"] <= SEQ_HI]
print(f"Re-rendering {len(plan)} chunks (seq {SEQ_LO}..{SEQ_HI}) without per-chunk loudnorm")

# Need to recover gen_text for each — plan.jsonl has it.
# Also need ref path/text per voice.
# Sort by sequence
plan.sort(key=lambda r: r["seq"])

# Group by voice
by_voice = {}
for r in plan:
    key = (r["voice"], r["run"], r["ckpt"])
    by_voice.setdefault(key, []).append(r)

# Synth voice by voice
output_paths = {}  # seq → path
for (vl, run, ckpt), entries in by_voice.items():
    print(f"\n=== loading {vl} ({run}/{ckpt}) for {len(entries)} chunks ===", flush=True)
    f5 = load_f5(run, ckpt)
    ref_path, ref_text = VOICE_REFS[vl]
    for r in entries:
        seq = r["seq"]
        speed = r.get("speed", 0.7)
        dst = OUT / f"chunk_{seq:04d}_{vl}.wav"
        synth_chunk(f5, ref_path, ref_text, r["text"], dst, speed=speed)
        output_paths[seq] = dst
        print(f"  ✓ seq={seq:>3d} [{vl}] speed={speed} {r['text'][:50]!r}", flush=True)
    del f5; gc.collect(); torch.cuda.empty_cache()

# Concat in seq order
ordered = [output_paths[s] for s in sorted(output_paths.keys())]
list_file = OUT / "concat.txt"
with list_file.open("w") as f:
    for p in ordered: f.write(f"file '{p.resolve()}'\n")

# Master pass: concat first, then ONE loudnorm
master = OUT / "test_section.wav"
subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-af", MASTER_DYNAMICS,
                "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le", str(master)],
               check=True)
print(f"\n✓ wrote {master} ({master.stat().st_size/1024/1024:.1f} MB)")
