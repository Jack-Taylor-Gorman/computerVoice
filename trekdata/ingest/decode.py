"""ffmpeg decode to 24 kHz mono 16-bit PCM WAV cache."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from trekdata.config import settings


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def decode_to_cache(src: Path, sample_rate: int | None = None) -> Path:
    sr = sample_rate or settings.target_sample_rate
    digest = sha256_file(src)
    out = settings.cache_dir / f"{digest}.{sr}.wav"
    if out.exists():
        return out
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ar", str(sr), "-ac", "1", "-f", "wav", "-acodec", "pcm_s16le", str(out)],
        check=True,
    )
    return out
