#!/usr/bin/env python3
"""Generate a Majel sample via OuteTTS zero-shot voice cloning."""
import sys
from pathlib import Path

REF_WAV = Path("dataset/clips/voy_all_computer_lines_pt1__0000.wav")
REF_TEXT = "There are fourteen varieties of tomato soup available from this replicator."
OUT = Path("/tmp/majel_new_sample.wav")
SAMPLE_TEXT = (
    "Working. Access granted. There are four results matching your query. "
    "Unable to comply. Please restate the command."
)

import outetts
# Force whisper onto CPU so the 1B LM can keep its VRAM
import outetts.whisper.transcribe as _wt
import whisper as _whisper
_orig_load = _whisper.load_model
_wt.transcribe_once_word_level.__globals__['whisper'].load_model = (
    lambda m, device=None: _orig_load(m, device="cpu")
)

cfg = outetts.ModelConfig.auto_config(
    model=outetts.Models.VERSION_1_0_SIZE_1B,
    backend=outetts.Backend.HF,
)
iface = outetts.Interface(config=cfg)

speaker = iface.create_speaker(audio_path=str(REF_WAV), transcript=REF_TEXT)

out = iface.generate(outetts.GenerationConfig(
    text=SAMPLE_TEXT,
    speaker=speaker,
    generation_type=outetts.GenerationType.CHUNKED,
))
out.save(str(OUT))
print(f"wrote {OUT}")
