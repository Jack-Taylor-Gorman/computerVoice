# Trek Dataset Builder — Roadmap

## Target
Fine-tune **F5-TTS** (MIT) on 15-30+ min of cleaned Majel Barrett computer-voice audio at 24 kHz mono.
Inference target: ~0.15× realtime on RTX 4060 8GB; fine-tune fits without gradient checkpointing.

## Stack
- Backend: FastAPI + SQLAlchemy 2.0 (async) + aiosqlite + arq (Redis)
- Frontend: Vite + React 18 + TS + wavesurfer.js 7 + TanStack Query + shadcn/ui
- ML: faster-whisper large-v3, silero-vad v5, WhisperX, DeepFilterNet3, pyloudnorm, speechbrain ECAPA, phonemizer (espeak-ng)

## Dataset schema
24 kHz mono 16-bit WAV, 1.5-15s clips, LJSpeech `metadata.csv` + JSONL sidecar with: transcript, normalized transcript, archetype, trigger_utterance, addressee, scene_context, source_episode, series, speaker_id, snr_db, lufs, noise_class, prosody_tag, word_alignments, phonemes, speaker_embedding.

## Pipeline
decode (ffmpeg 24kHz mono) → silero-vad → faster-whisper large-v3 (word timestamps) → WhisperX align → pyloudnorm measure → WADA SNR → DeepFilterNet3 (conditional) → ECAPA embed → phonemizer IPA → archetype suggest → human label → approve → export.

## Archetypes (16)
See `trekdata/archetypes.py`. Keys 1-9 are keyboard shortcuts; remainder via dropdown.

## Dual-use
Each clip stores `transcript` + `trigger_utterance` → parallel corpus for upgrading `computerize.py`'s `fallback_strip()` to a prompted-LLM style-transfer step.

## Phase order
1. Scaffold (this) ✅
2. Ingest pipeline (decode, VAD, transcribe, align, SNR, LUFS, embed, phoneme) — see `trekdata/ingest/`
3. Frontend Clip Editor (waveform trim + 1-9 archetype + J/K nav + Enter approve / X reject)
4. Batch import + Record screens
5. Export (LJSpeech + HF manifest + train/val/test splits)
6. F5-TTS fine-tune wrapper (`trekdata train --dataset <path>`)
7. computerize.py LLM fallback using collected trigger_utterance↔transcript pairs

## Run
```
pip install -e '.[trekdata]'
redis-server &
arq trekdata.worker.WorkerSettings &
uvicorn trekdata.main:app --host 127.0.0.1 --port 7862
```
