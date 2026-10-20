# Lean voice server: to-do

## Done (iteration 1, 2026-10-20)

- [x] Measure the baseline: cold load, RSS/VRAM, warm latency, and baseline
      WAVs in `.loop/baseline/` (see `LEAN_REPORT.md`).
- [x] Convert the checkpoint to EMA-only fp16 safetensors
      (`scripts/lean_convert_ckpt.py`, output in
      `venv/lib/python3.10/ckpts/majel_all_run2/model_600_ema_fp16.safetensors`).
      The worker falls back to the `.pt` if that file is missing.
- [x] Lean worker `f5_worker.py`: stubbed training/ASR imports, built on the
      GPU, local vocoder, offline Hugging Face hub, `malloc_trim` after load.
- [x] Stdlib front `f5_daemon.py` owning the socket, with on-demand start,
      idle unload (`MAJEL_F5_IDLE_S`), a `{"warm": true}` request and SIGTERM
      teardown. The watchdog is unchanged apart from comments.
- [x] Verified live: the socket request, `speak.py`, Whisper transcripts and
      SIGTERM/SIGKILL teardown.
- [x] `user_prompt.sh` sends a fire-and-forget warm request so the reply
      doesn't wait for a cold start.
- [x] Idle unload, warm restart and SIGTERM verified with a stand-in worker
      on a private socket.

## Next

1. **Observe the live 600 s unload.** It was verified only with a stand-in
   worker, because real traffic kept resetting the live timer. Check
   `grep "unloading model" /tmp/majel_f5_daemon.log`, and confirm the front's
   RSS is around 12 MB and that `nvidia-smi` lists no `f5_worker.py`. The
   live front was started before the `MAJEL_F5_SOCK`/`MAJEL_F5_WORKER`
   overrides were added; with defaults the code is identical, and the next
   session start picks up the new file.
2. **Warm-up hook end to end from the unloaded state.** The snippet was
   checked against the live socket while the worker was loaded (rc 0). Once
   the worker is unloaded, run `echo '{}' | bash user_prompt.sh` (it beeps),
   confirm the log shows `worker pid=... starting`, and confirm a request
   about 3 s later takes warm latency.
3. Bonus: active RSS. The worker uses 1.87 GB, against a floor of about
   1.05 GB for torch and CUDA. Look at what the remaining ~0.8 GB is (cuDNN
   workspace, kernel images, the CPU heap) and whether
   `torch.cuda.empty_cache()` after each request, or skipping
   `torch.backends.cudnn.benchmark`, changes RSS or VRAM without hurting
   latency.
4. Optional: the old 3.4 GB `model_600.pt` is still needed for training. Do
   not delete it; it is just no longer read at inference time.
5. Approach 4 (ONNX/TensorRT) only if active footprint becomes a priority;
   targets 1–3 are already met without it.
