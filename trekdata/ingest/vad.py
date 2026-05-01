"""Silero-VAD segmentation into speech spans."""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache

import numpy as np
import soundfile as sf


@lru_cache(maxsize=1)
def _get_model():
    import torch
    model, utils = torch.hub.load(
        repo_or_dir="snakers4/silero-vad",
        model="silero_vad",
        trust_repo=True,
        onnx=False,
    )
    return model, utils


def segment(wav_path: Path, min_speech_ms: int = 1500, min_silence_ms: int = 300) -> list[tuple[float, float]]:
    import torch
    audio, sr = sf.read(str(wav_path), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        import librosa
        audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
        sr = 16000
    model, utils = _get_model()
    get_speech_timestamps = utils[0]
    spans = get_speech_timestamps(
        torch.from_numpy(audio),
        model,
        sampling_rate=sr,
        min_speech_duration_ms=min_speech_ms,
        min_silence_duration_ms=min_silence_ms,
    )
    return [(s["start"] / sr, s["end"] / sr) for s in spans]
