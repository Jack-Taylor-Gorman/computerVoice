# Lessons from making Majel's voice server lean

Newest first. Each heading states the lesson.

## 2026-10-20 — `f5_tts.api` is a training import; build inference from `utils_infer` with stubs

Importing `f5_tts.api` took 7.6 s and pulled in TensorFlow, Keras,
torchvision, wandb and the Google Cloud SDK, through `transformers` (Whisper
ASR for untranscribed reference clips), `f5_tts.model.trainer` and
`cached_path`. With a fixed reference transcript none of that runs. Stubbing
`transformers`, `f5_tts.model.trainer`, `matplotlib` and `librosa` in
`sys.modules` and lazily importing `einx` gets the worker's imports to about
1.7 s. A stub's module `__getattr__` must raise `AttributeError`, not anything
else: `inspect.getmodule` calls `hasattr()` on every loaded module, and any
other exception escapes.

## 2026-10-20 — The training checkpoint is the cold-start and OOM problem

`model_600.pt` is 3.4 GB: EMA weights, online weights and optimiser state,
all fp32. The stock loader `torch.load`s it with `map_location=device`, so the
whole file lands on the GPU before it is filtered. The EMA weights alone, as
fp16 safetensors, are 0.67 GB and load in under half a second. f5_tts casts
to fp16 on CUDA anyway, so pre-casting changes nothing the model sees.
Convert with `torch.load(..., mmap=True)` so the 3.4 GB file stays in the page
cache rather than in anonymous RAM.

## 2026-10-20 — The CUDA runtime alone costs about 1 GB of RSS

`import torch`, a CUDA context, cuBLAS, cuDNN and cuFFT come to about 1.05 GB
of RSS before any model is loaded. Trimming inside a loaded process cannot get
near the 150 MB idle target; only exiting the process can. That is why the
front/worker split is the main prize.

## 2026-10-20 — Every Claude session start forces a voice reload

`session_start.sh` deletes `/tmp/majel_f5.sock` on every SessionStart, and loop
iterations count. The watchdog then kills the daemon because its socket is
gone. Its `pkill -f "$DIR/f5_daemon.py"` never matches, because "(copy)" in
the path is a regex group. So each new Claude session costs a full model
reload. That is cheap now (a front restart, then a cold worker on first
speech), but it explains the frequent reloads in `/tmp/majel_f5_daemon.log`.

## 2026-10-20 — Hand the worker a pipe, not a second socket

The front talks to the worker over the worker's stdin and stdout, with one
JSON line each way. The worker moves the real stdout to a private file
descriptor and points fd 1 at stderr, so library prints can't corrupt the
protocol. Stdin EOF is a clean shutdown signal. A parent-PID watch thread
covers a front that dies while the model is still loading. There is no
second socket file to clean up, and none can go stale.

## 2026-10-20 — Editing a running bash script is safe only inside an already-parsed loop

`run_f5_daemon.sh` was edited while the watchdog ran. Bash had already parsed
the infinite `while true` loop, so nothing broke. Edits that change
statements bash has not read yet would be picked up mid-run at shifted
offsets.
