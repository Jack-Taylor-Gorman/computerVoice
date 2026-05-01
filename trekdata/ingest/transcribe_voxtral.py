"""Voxtral-Mini-3B-2507 transcription backend.

Drop-in replacement for trekdata.ingest.transcribe with the same return shape
(`{text, words, language}`). Voxtral is a multimodal LLM — it produces text
directly but does not emit native word timestamps. We therefore pair it with
WhisperX's CTC aligner (already used in trekdata.ingest.align) to recover
word-level timing from the Voxtral text.

Activation: set settings.whisper_model = "voxtral" in trekdata/.env or via
TRANSCRIBE_BACKEND=voxtral env var. Falls back to faster-whisper otherwise.

VRAM: 3B params bf16 ≈ 6.0 GB. Fits an 8 GB RTX 4060 with whisperx-align
loaded too (whisperx wav2vec2-base ≈ 350 MB). For tighter fits, swap to the
RedHatAI FP8-dynamic variant (about 3.5 GB) by setting
VOXTRAL_MODEL=RedHatAI/Voxtral-Mini-3B-2507-FP8-dynamic.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from trekdata.config import settings


VOXTRAL_MODEL_ID = os.environ.get("VOXTRAL_MODEL", "mistralai/Voxtral-Mini-3B-2507")


@lru_cache(maxsize=1)
def _load():
    import torch
    from transformers import VoxtralForConditionalGeneration, VoxtralProcessor

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.bfloat16 if device == "cuda" else torch.float32

    processor = VoxtralProcessor.from_pretrained(VOXTRAL_MODEL_ID)
    model = VoxtralForConditionalGeneration.from_pretrained(
        VOXTRAL_MODEL_ID, torch_dtype=dtype, device_map=device
    )
    model.eval()
    return processor, model, device


def transcribe(wav_path: Path, start_s: float = 0.0, end_s: float | None = None) -> dict:
    import torch

    processor, model, device = _load()

    inputs = processor.apply_transcription_request(
        language="en",
        audio=str(wav_path),
        model_id=VOXTRAL_MODEL_ID,
    ).to(device, dtype=model.dtype)

    with torch.inference_mode():
        out_ids = model.generate(**inputs, max_new_tokens=512, do_sample=False)
    text = processor.batch_decode(out_ids[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)
    text = (text[0] if text else "").strip()

    words: list[dict] = []
    if text:
        try:
            from trekdata.ingest.align import align as _align
            seg = [{"start": start_s, "end": end_s if end_s is not None else 0.0, "text": text}]
            words = _align(wav_path, seg)
        except Exception:
            words = []

    return {"text": text, "words": words, "language": "en"}
