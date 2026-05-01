"""pyloudnorm integrated-LUFS measurement + normalizer."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def measure_lufs(wav_path: Path) -> float:
    import pyloudnorm as pyln
    data, sr = sf.read(str(wav_path))
    if data.ndim > 1:
        data = data.mean(axis=1)
    meter = pyln.Meter(sr)
    return float(meter.integrated_loudness(data))


def normalize_to(wav_path: Path, out_path: Path, target_lufs: float = -23.0, true_peak_db: float = -1.0) -> float:
    import pyloudnorm as pyln
    data, sr = sf.read(str(wav_path))
    if data.ndim > 1:
        data = data.mean(axis=1)
    meter = pyln.Meter(sr)
    loudness = meter.integrated_loudness(data)
    normalized = pyln.normalize.loudness(data, loudness, target_lufs)
    peak = float(np.max(np.abs(normalized)) + 1e-12)
    ceiling = 10 ** (true_peak_db / 20.0)
    if peak > ceiling:
        normalized = normalized * (ceiling / peak)
    sf.write(str(out_path), normalized, sr, subtype="PCM_16")
    return float(loudness)
