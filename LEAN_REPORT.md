# Majel voice server: lean report

## Status (2026-10-20, iteration 1)

The voice server is now split into a stdlib front (`f5_daemon.py`, about
12 MB of RSS and no VRAM) that always owns `/tmp/majel_f5.sock`, and a model
worker (`f5_worker.py`) that the front starts on the first request and stops
after ten idle minutes (`MAJEL_F5_IDLE_S`, default 600). The first three
targets are met on measurement: the idle footprint is 12 MB of RSS with no
VRAM, a cold first utterance takes 3.5–3.9 s end to end, and warm latency is
unchanged. The fourth target (same voice) holds by construction, because the
weights are bit-identical, and all transcripts match.

The idle-unload logic was verified with the real front driving a stand-in
worker on a private socket (`MAJEL_F5_SOCK`, `MAJEL_F5_WORKER`,
`MAJEL_F5_IDLE_S=15`). The worker was stopped cleanly after 15 idle seconds,
and a later warm request restarted it. The 600 s unload has **not yet been
observed on the live service**. Other Claude sessions spoke eight times
between 12:45 and 12:57, each resetting the timer, and with only 2.2 GB of
RAM free a second model copy for a separate test was ruled out.

## Numbers

All times are wall-clock from a stdlib client on the live socket
(`scripts/lean_bench.py`). The phrases are "Verification check." (19
characters), "Working." (8), "Build complete. All tests passed." (33) and
"Accessing the library computer for the latest records." (54).

| Measure | Before (single-process daemon) | After (front + worker) |
|---|---|---|
| Idle RSS | 2.84 GB (1.86 GB at ready, grows after the first request) | 12 MB (front only) |
| Idle VRAM | 874–908 MiB | 0 |
| Process start to ready | about 20 s (7.6 s of it importing `f5_tts.api`) | worker ready in 2.2 s |
| First utterance after idle, end to end | not applicable (always loaded); a reload took 20 s to over a minute | 3.49 s and 3.94 s (two runs) |
| Warm, 8 / 19 / 33 / 54 characters | 0.83 / 0.82–0.86 / 1.05–1.07 / 1.30 s | 0.85–0.87 / 0.84 / 1.08–1.09 / 1.33–1.34 s |
| Active RSS (worker) | 2.84 GB | 1.87 GB (1.33 GB anonymous) |
| Active VRAM | 874–908 MiB | 892 MiB |
| Checkpoint read at load | 3.38 GB `.pt` (EMA + online weights + optimiser state, fp32), loaded with `map_location="cuda"` | 0.67 GB EMA-only fp16 safetensors |
| `speak.py` "Lean check." including playback | not measured | 4.93 s (cold worker) |

### Where the time and memory went

- **Imports.** `f5_tts.api` pulled in `cached_path` (and through it the Google
  Cloud SDK from `~/.local`), `transformers` (Whisper ASR, which drags in
  TensorFlow, Keras and torchvision) and the training `Trainer` (wandb). That
  came to 7.6 s and most of the extra RSS. None of it is used for inference
  with a fixed reference transcript. After stubbing those, plus matplotlib and
  librosa and lazily importing einx, the worker's imports take about 1.7 s, of
  which `import torch` is about 0.8–1.0 s.
- **Checkpoint.** `model_600.pt` is the full training checkpoint.
  `scripts/lean_convert_ckpt.py` extracts the EMA weights as fp16 safetensors.
  f5_tts already ran the model in fp16 on CUDA (its `load_checkpoint` casts to
  fp16 on any GPU of compute capability 7 or above), so this is the same cast
  done ahead of time, and the weights the GPU sees are identical. Model load
  now takes 0.2–0.4 s with a warm page cache (1.6 s once when the cache was
  cold). The old path also copied the whole 3.4 GB checkpoint, optimiser state
  included, onto the GPU during load. That is the likely cause of the
  out-of-memory restart loop seen earlier today; it was inferred from the
  code, not measured.
- **Construction.** The model is now constructed directly on the GPU instead
  of initialising 337M fp32 parameters on the CPU first.
- **Vocoder.** Loaded from the local Hugging Face cache with
  `HF_HUB_OFFLINE=1`, so there is no network check.
- **Fixed floor.** In a bare probe, `import torch` plus a CUDA context,
  cuBLAS, cuDNN and cuFFT come to about 1.05 GB of RSS (0.66 GB anonymous).
  That is the floor for any loaded worker, and the main reason active RSS
  cannot drop much below about 1.5 GB without a different runtime.
- **First request.** The first synthesis in a fresh worker takes about 1.3 s
  against 0.85 s warm, because of CUDA kernel loading and warm-up.

### Voice check

Whisper `small.en` (CPU, int8) transcripts of baseline and lean outputs all
match the text. "Verification check" came back as "Verification checked"
in two of the first lean samples. Six further lean samples all transcribed
correctly, so this is sampling variance; F5 draws a fresh random seed per
request. Durations are identical to baseline (2.13 / 2.10 / 3.34 / 5.15 s) and
RMS varies within the same range from run to run (0.07–0.13).

### Lifecycle checks

- SIGTERM to the front (what the watchdog sends when the socket vanishes)
  stopped the worker within 1.5 s, and the watchdog relaunched the front.
- SIGKILL to the front left the worker orphaned; it exited within 1 s through
  its parent-PID watch.
- A worker killed on its own was noticed by the front's reaper, and the next
  request started a fresh worker.
