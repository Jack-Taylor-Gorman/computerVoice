"""Transcription dispatcher.

Default backend: faster-whisper large-v3 (word-level timestamps).
Voxtral backend selectable via env: TRANSCRIBE_BACKEND=voxtral or
settings.whisper_model="voxtral". See trekdata.ingest.transcribe_voxtral.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from trekdata.config import settings


def _backend() -> str:
    env = os.environ.get("TRANSCRIBE_BACKEND", "").lower().strip()
    if env in ("voxtral", "faster-whisper", "fw"):
        return env
    if settings.whisper_model.lower().startswith("voxtral"):
        return "voxtral"
    return "faster-whisper"


@lru_cache(maxsize=1)
def _get_model():
    from faster_whisper import WhisperModel
    return WhisperModel(
        settings.whisper_model,
        device=settings.whisper_device,
        compute_type=settings.whisper_compute_type,
    )


def transcribe(wav_path: Path, start_s: float = 0.0, end_s: float | None = None) -> dict:
    if _backend() == "voxtral":
        from trekdata.ingest.transcribe_voxtral import transcribe as _vt
        return _vt(wav_path, start_s, end_s)
    model = _get_model()
    segments, info = model.transcribe(
        str(wav_path),
        language="en",
        beam_size=5,
        word_timestamps=True,
        vad_filter=False,
        clip_timestamps=[start_s, end_s] if end_s is not None else None,
    )
    text_parts: list[str] = []
    words: list[dict] = []
    for seg in segments:
        text_parts.append(seg.text)
        for w in seg.words or []:
            words.append({"word": w.word.strip(), "start": w.start, "end": w.end, "conf": w.probability})
    return {"text": " ".join(t.strip() for t in text_parts).strip(), "words": words, "language": info.language}
