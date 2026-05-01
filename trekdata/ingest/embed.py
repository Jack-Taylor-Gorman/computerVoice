"""ECAPA-TDNN speaker embedding via speechbrain."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np


@lru_cache(maxsize=1)
def _get_encoder():
    from speechbrain.inference.speaker import EncoderClassifier
    return EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb", run_opts={"device": "cuda"})


def embed(wav_path: Path) -> bytes:
    import torchaudio
    enc = _get_encoder()
    sig, sr = torchaudio.load(str(wav_path))
    if sr != 16000:
        sig = torchaudio.functional.resample(sig, sr, 16000)
    emb = enc.encode_batch(sig).squeeze().detach().cpu().numpy().astype(np.float32)
    return emb.tobytes()
