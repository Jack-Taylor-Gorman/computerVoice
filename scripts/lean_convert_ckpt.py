#!/usr/bin/env python3
"""Extract the EMA weights from a full F5 training checkpoint as fp16 safetensors.

The training checkpoint (~3.4 GB) carries the optimiser state and fp32
weights; inference only needs the EMA weights, which f5_tts casts to fp16
on CUDA anyway, so the cast here is the same one the loader would do.

Usage: lean_convert_ckpt.py SRC.pt DST.safetensors
"""
import sys

import torch
from safetensors.torch import save_file

src, dst = sys.argv[1], sys.argv[2]
# mmap keeps the 3.4 GB file in the page cache instead of anonymous RAM.
ckpt = torch.load(src, map_location="cpu", weights_only=False, mmap=True)
ema = ckpt["ema_model_state_dict"]
out = {}
for k, v in ema.items():
    if k in ("initted", "step"):
        continue
    k = k.replace("ema_model.", "")
    if k in ("mel_spec.mel_stft.mel_scale.fb", "mel_spec.mel_stft.spectrogram.window"):
        continue
    out[k] = v.to(torch.float16).contiguous() if v.is_floating_point() else v.contiguous()
save_file(out, dst, metadata={"source": src, "ema": "1", "dtype": "float16"})
print(f"{len(out)} tensors, {sum(t.numel() for t in out.values())/1e6:.1f}M params -> {dst}")
