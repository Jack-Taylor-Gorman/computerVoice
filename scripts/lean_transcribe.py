#!/usr/bin/env python3
"""Transcribe WAVs with faster-whisper small.en on the CPU.

Audio is decoded with ffmpeg to 16 kHz float32, because faster-whisper's own
PyAV decoder is broken with the PyAV version installed in this venv.
Usage: lean_transcribe.py WAV [WAV ...]
"""
import subprocess
import sys

import numpy as np
from faster_whisper import WhisperModel


def load(path):
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", path, "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32)


m = WhisperModel("small.en", device="cpu", compute_type="int8")
for p in sys.argv[1:]:
    segs, _ = m.transcribe(load(p), language="en", beam_size=5)
    print(f"{p.split('/')[-2]}/{p.split('/')[-1]}: {' '.join(s.text.strip() for s in segs)}", flush=True)
