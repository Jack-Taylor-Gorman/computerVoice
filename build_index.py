#!/usr/bin/env python3
"""Build a faiss index from Majel voice clips for RVC retrieval.

Extracts hubert features from every mp3 in sounds/computer/voice/, builds an
IVF-Flat faiss index compatible with rvc-python, writes it next to Majel.pth.
"""
import glob
import os
import sys
from pathlib import Path

import torch as _torch
_orig = _torch.load
_torch.load = lambda *a, **kw: _orig(*a, **{**kw, "weights_only": False})

import faiss
import librosa
import numpy as np
from fairseq import checkpoint_utils

ROOT = Path(__file__).resolve().parent
VOICE_DIR = ROOT / "sounds" / "computer" / "voice"
OUT_INDEX = ROOT / "Majel" / "added_IVF_Flat_Majel_v2.index"


def find_hubert() -> str:
    for p in [
        ROOT / "base_models" / "hubert_base.pt",
        Path.home() / ".cache" / "rvc" / "hubert_base.pt",
    ]:
        if p.exists():
            return str(p)
    for hit in glob.glob(str(ROOT / "**" / "hubert_base.pt"), recursive=True):
        return hit
    raise FileNotFoundError("hubert_base.pt not found; run speak.py once first.")


def load_hubert(path: str, device: str):
    models, _, _ = checkpoint_utils.load_model_ensemble_and_task([path], suffix="")
    m = models[0].to(device).eval()
    if device.startswith("cuda"):
        m = m.half()
    return m


def extract(model, wav16k: np.ndarray, device: str) -> np.ndarray:
    with _torch.no_grad():
        feats = _torch.from_numpy(wav16k).to(device)
        if device.startswith("cuda"):
            feats = feats.half()
        feats = feats.view(1, -1)
        pad = _torch.BoolTensor(feats.shape).to(device).fill_(False)
        inputs = {"source": feats, "padding_mask": pad, "output_layer": 12}
        logits = model.extract_features(**inputs)
        out = logits[0]
    return out.squeeze(0).float().cpu().numpy()


def main() -> int:
    device = "cuda:0" if _torch.cuda.is_available() else "cpu"
    hubert_path = find_hubert()
    print(f"hubert: {hubert_path}")
    model = load_hubert(hubert_path, device)

    files = sorted(glob.glob(str(VOICE_DIR / "*.mp3")))
    print(f"clips: {len(files)}")
    feats = []
    for i, f in enumerate(files, 1):
        try:
            wav, _ = librosa.load(f, sr=16000, mono=True)
            if len(wav) < 16000 * 0.2:
                continue
            v = extract(model, wav, device)
            feats.append(v)
            print(f"  [{i}/{len(files)}] {os.path.basename(f)}: {v.shape}")
        except Exception as e:
            print(f"  skip {f}: {e}")

    big = np.concatenate(feats, axis=0).astype(np.float32)
    print(f"features: {big.shape}")

    n = big.shape[0]
    nlist = max(1, min(int(np.sqrt(n)), 1024))
    quantizer = faiss.IndexFlatL2(big.shape[1])
    index = faiss.IndexIVFFlat(quantizer, big.shape[1], nlist)
    index.train(big)
    index.add(big)
    faiss.write_index(index, str(OUT_INDEX))
    print(f"wrote {OUT_INDEX} (nlist={nlist}, vectors={index.ntotal})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
