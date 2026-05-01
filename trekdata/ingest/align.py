"""WhisperX forced alignment for precise word timestamps."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from trekdata.config import settings


@lru_cache(maxsize=1)
def _get_aligner():
    import whisperx
    model, metadata = whisperx.load_align_model(language_code="en", device=settings.whisper_device)
    return model, metadata


def align(wav_path: Path, transcript_segments: list[dict]) -> list[dict]:
    """Align existing transcript segments to audio. Returns word-level timestamps."""
    import whisperx
    model, metadata = _get_aligner()
    audio = whisperx.load_audio(str(wav_path))
    result = whisperx.align(transcript_segments, model, metadata, audio, settings.whisper_device, return_char_alignments=False)
    return result.get("word_segments", [])
