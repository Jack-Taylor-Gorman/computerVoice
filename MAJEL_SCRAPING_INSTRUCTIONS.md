# Majel Barrett Voice Dataset — Scraping Instructions

End-to-end pipeline for mining clean Majel computer-voice clips from Star Trek
episodes and producing a training-ready dataset for GPT-SoVITS / StyleTTS2 /
RVC fine-tuning.

## 1. Where to place episode files

Put video files under:

```
/home/jackgorman/Desktop/Claude_Projects/computerVoice/dataset/episodes/TNG/
/home/jackgorman/Desktop/Claude_Projects/computerVoice/dataset/episodes/VOY/
```

Any nested structure is fine — `mine_majel.py` walks recursively and picks up
`.mkv`, `.mp4`, `.avi`, `.m4v`, `.mov`, `.ts`. Examples that all work:

```
TNG/Season 1/TNG.S01E01.Encounter.at.Farpoint.mkv
TNG/Season.5/TNG.S05E25.The.Inner.Light.mp4
VOY/VOY_1x01_Caretaker.mp4
VOY/Season 4/Voyager.S04E08.Year.of.Hell.mkv
```

Subtitles are not required — WhisperX will transcribe the audio itself.

## 2. One-time setup

```bash
cd /home/jackgorman/Desktop/Claude_Projects/computerVoice
./venv/bin/pip install -r scripts/requirements-mine.txt
```

Get a Hugging Face token (free) and accept the pyannote terms:

1. https://huggingface.co/settings/tokens — create a read token.
2. https://huggingface.co/pyannote/speaker-diarization-3.1 — click "Agree".
3. Export the token so the scraper can find it:

```bash
export HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Add that line to `~/.bashrc` if you want it persisted.

## 3. Create a Majel reference clip

The embedding gate compares every candidate segment against a clean Majel
reference. 5–30 seconds is ideal. Easiest source — your existing stock clips:

```bash
cd /home/jackgorman/Desktop/Claude_Projects/computerVoice
ffmpeg -i sounds/computer/voice/accessinglibrarycomputerdata_clean.mp3 \
    -ar 16000 -ac 1 dataset/majel_ref.wav
```

If that clip is too short, concatenate several:

```bash
ffmpeg -i "concat:sounds/computer/voice/accessinglibrarycomputerdata_clean.mp3|sounds/computer/voice/pleaserestatecommand_ep.mp3|sounds/computer/voice/specifyparameters.mp3" \
    -ar 16000 -ac 1 dataset/majel_ref.wav
```

## 4. Run the scrape

```bash
cd /home/jackgorman/Desktop/Claude_Projects/computerVoice
./scripts/mine_majel.py \
    --episodes dataset/episodes \
    --reference dataset/majel_ref.wav \
    --out dataset \
    --device cuda:0
```

Replace `cuda:0` with `cpu` if you don't have a GPU (much slower).

Tuning knob — `--threshold` (default 0.72, range 0.0–1.0):
- Too many Lwaxana / Deanna Troi / other speakers sneaking in → raise to 0.78.
- Too few clips kept → lower to 0.65.

## 5. What gets produced

```
dataset/
├── episodes/           # Your input videos (unchanged)
├── raw/                # Per-episode 16k mono wav cache
├── clips/              # Final trimmed Majel clips (22.05k mono wav)
├── cache/              # Per-episode JSON cache (resumable; delete to reprocess)
└── manifests/
    └── majel.jsonl     # One JSON line per clip: text, times, cosine, source
```

The pipeline is resumable — rerunning the command skips episodes whose cache
file already exists. Add `--overwrite` to force reprocessing.

## 6. Expected yield

Typical TNG + VOY full-series run:
- ~350 episodes processed
- ~1000–1800 computer-voice clips retained after embedding gate
- 60–120 minutes of clean, labeled audio

That's well above the minimum for a strong GPT-SoVITS v4 or StyleTTS2 fine-tune.

## 7. Pipeline phases (what runs under the hood)

1. **Demux** — `ffmpeg` → 16k mono wav, cached per episode.
2. **ASR + align + diarize** — WhisperX large-v3 produces word-level timestamps
   and speaker labels.
3. **Candidate selection** — pick segments where the prior turn addresses
   "Computer,…" OR the text matches known computer-response patterns
   (`working`, `affirmative`, `unable to comply`, `specify parameters`,
   stardate/deck/life-sign numerics, etc.).
4. **Embedding gate** — Resemblyzer cosine-match each candidate vs the Majel
   reference. Rejects Lwaxana Troi (same actress, different prosody) and any
   spillover from the crew.
5. **Export** — `ffmpeg` cuts each keeper to 22.05kHz mono PCM with a small
   pad, writes to `dataset/clips/<episode>__<idx>.wav`, records metadata.

## 8. After the scrape — training a model

Your `dataset/manifests/majel.jsonl` format is compatible with:
- **GPT-SoVITS v4** — convert to its `list` format (1 line per clip:
  `<wav>|<speaker>|<lang>|<text>`). Fine-tune for 8–20 hrs on a 3090-class GPU.
- **StyleTTS2** — its training script reads a similar manifest with minor
  tweaks.
- **RVC v2 Applio fork** — only needs audio, text is ignored; point training
  at `dataset/clips/` directly.

Once trained, swap the TTS stage in `speak.py` — replace the edge-tts call
with your model's inference and bypass RVC entirely (direct TTS captures
Majel's prosody, not just timbre).

## 9. Troubleshooting

- **Every episode "FAILED: load_model"** — install torch matching your CUDA
  version: `./venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu121`.
- **0 clips kept** — lower `--threshold` to 0.6 and inspect `dataset/cache/*.json`
  to see what segments were considered.
- **Lwaxana sneaking in** — raise threshold to 0.78; she's the primary
  false-positive since she's also Majel.
- **Music bed on some clips** — add a UVR-MDX-Net cleanup step; the pipeline
  leaves hooks for this but skips it by default.
- **Out of GPU memory** — set `WHISPER_MODEL=medium` env var to use a smaller
  Whisper model.

## 10. Files involved

- `scripts/mine_majel.py` — main scraper
- `scripts/requirements-mine.txt` — Python deps
- `dataset/episodes/` — your input videos
- `dataset/majel_ref.wav` — reference clip you create
- `dataset/manifests/majel.jsonl` — final output manifest
