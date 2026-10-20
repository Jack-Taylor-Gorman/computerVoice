#!/usr/bin/env python3
"""Heavy half of the Majel F5-TTS voice server: model + vocoder on the GPU.

Started on demand by f5_daemon.py (the always-on front that owns
/tmp/majel_f5.sock) and stopped by it after an idle timeout, so the ~3 GB of
RAM and ~0.9 GB of VRAM this process needs are only held while Majel talks.

Talks to the front over its own stdin/stdout, one JSON object per line:
  worker -> front : {"ready": true}            once, after the model loads
  front -> worker : {"text": "...", "dst": "/path.wav"}
  worker -> front : {"ok": true} | {"ok": false, "err": "..."}
EOF on stdin, or the front dying, makes the worker exit.

Env (same as the old single-process daemon):
  MAJEL_F5_RUN     finetune run name (default: majel_all_run2)
  MAJEL_F5_CKPT    checkpoint stem inside that run (default: model_600)
  MAJEL_F5_REF     filename inside dataset/clips_curated/ to use as ref clip
  MAJEL_F5_REFTEXT transcript of that ref clip
  MAJEL_F5_SPEED   speed multiplier (default 0.7)
  MAJEL_CPU=1      force CPU
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import types
from pathlib import Path

T0 = time.monotonic()

# The protocol owns the real stdout; everything else that prints (f5_tts's
# loaders, tqdm, warnings) goes to stderr, which the front sends to the log.
_proto = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr

os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _log(msg: str) -> None:
    sys.stderr.write(f"majel_f5_worker: {msg} (+{time.monotonic() - T0:.2f}s)\n")
    sys.stderr.flush()


def _watch_parent() -> None:
    # stdin EOF only arrives when the worker next reads; this also covers a
    # front that dies while the model is still loading or mid-synthesis.
    ppid = os.getppid()
    while True:
        time.sleep(2)
        if os.getppid() != ppid:
            os._exit(0)


threading.Thread(target=_watch_parent, daemon=True).start()


def _stub(name: str, **attrs) -> None:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod


def _unavailable(*_a, **_k):
    raise RuntimeError("not available in the lean Majel worker")


# f5_tts.infer.utils_infer imports transformers (for Whisper ASR of an
# untranscribed reference clip, which TensorFlow and torchvision ride in on)
# and f5_tts.model imports the Trainer (wandb). Inference uses neither: the
# reference text is always given. Stubbing them saves ~5 s and ~1 GB of RSS.
_stub("transformers", pipeline=_unavailable)
_stub("f5_tts.model.trainer", Trainer=None)
# matplotlib only draws spectrograms and librosa's mel filters only serve the
# BigVGAN vocoder; Majel uses Vocos. Together they cost ~0.5 s of import.
_stub("matplotlib", use=lambda *a, **k: None)
_stub("matplotlib.pylab")
sys.modules["matplotlib"].pylab = sys.modules["matplotlib.pylab"]
_stub("librosa")
_stub("librosa.filters", mel=_unavailable)
sys.modules["librosa"].filters = sys.modules["librosa.filters"]


def _lazy(name: str) -> None:
    # x_transformers imports einx (~0.55 s) at the top but only calls it in
    # features F5 doesn't use; defer the real import to first attribute use.
    import importlib.util
    spec = importlib.util.find_spec(name)
    if spec is None or spec.loader is None:
        return
    spec.loader = importlib.util.LazyLoader(spec.loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)


_lazy("einx")

import torch  # noqa: E402

from f5_tts.infer import utils_infer  # noqa: E402
from f5_tts.model.backbones.dit import DiT  # noqa: E402
from f5_tts.model.utils import seed_everything  # noqa: E402

import pronounce  # noqa: E402

_log("imports done")

ROOT = Path(__file__).resolve().parent
RUN = os.environ.get("MAJEL_F5_RUN", "majel_all_run2")
CKPT_NAME = os.environ.get("MAJEL_F5_CKPT", "model_600")
CKPT_DIR = ROOT / "venv" / "lib" / "python3.10" / "ckpts" / RUN
# scripts/lean_convert_ckpt.py writes the EMA weights as fp16 safetensors
# (0.67 GB instead of the 3.4 GB training checkpoint); fall back to the .pt.
CKPT_FAST = CKPT_DIR / f"{CKPT_NAME}_ema_fp16.safetensors"
CKPT_PATH = CKPT_FAST if CKPT_FAST.exists() else CKPT_DIR / f"{CKPT_NAME}.pt"
VOCAB_PATH = ROOT / "venv" / "lib" / "python3.10" / "data" / f"{RUN}_pinyin" / "vocab.txt"
REF_NAME = os.environ.get(
    "MAJEL_F5_REF",
    "001__0.985__2.1s__Accessing_Library_Computer_Data.wav",
)
REF_AUDIO = ROOT / "dataset" / "clips_curated" / REF_NAME
REF_TEXT = os.environ.get("MAJEL_F5_REFTEXT", "Accessing Library Computer Data")
SPEED = float(os.environ.get("MAJEL_F5_SPEED", "0.7"))
DEVICE = "cuda" if os.environ.get("MAJEL_CPU") != "1" else "cpu"
VOCOS_CACHE = Path.home() / ".cache" / "huggingface" / "hub" / "models--charactr--vocos-mel-24khz" / "snapshots"

# Must match configs/F5TTS_v1_Base.yaml (model.arch), which f5_tts.api reads.
ARCH = dict(
    dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, text_mask_padding=True,
    qk_norm=None, conv_layers=4, pe_attn_head=None, attn_backend="torch",
    attn_mask_enabled=False, checkpoint_activations=False,
)


def _vocos_dir() -> str | None:
    for snap in sorted(VOCOS_CACHE.glob("*")):
        if (snap / "config.yaml").exists() and (snap / "pytorch_model.bin").exists():
            return str(snap)
    return None


def build():
    u = utils_infer
    vdir = _vocos_dir()
    vocoder = u.load_vocoder("vocos", vdir is not None, vdir or "", DEVICE)
    _log("vocoder loaded")

    vocab_char_map, vocab_size = u.get_tokenizer(str(VOCAB_PATH), "custom")
    # Same construction as utils_infer.load_model, but built straight on the
    # device instead of initialising 337M fp32 parameters on the CPU first.
    with torch.device(DEVICE):
        model = u.CFM(
            transformer=DiT(**ARCH, text_num_embeds=vocab_size, mel_dim=u.n_mel_channels),
            mel_spec_kwargs=dict(
                n_fft=u.n_fft, hop_length=u.hop_length, win_length=u.win_length,
                n_mel_channels=u.n_mel_channels, target_sample_rate=u.target_sample_rate,
                mel_spec_type="vocos",
            ),
            odeint_kwargs=dict(method=u.ode_method),
            vocab_char_map=vocab_char_map,
        )
    model = u.load_checkpoint(model, str(CKPT_PATH), DEVICE, dtype=None, use_ema=True)
    _log(f"model loaded from {CKPT_PATH.name}")
    return model, vocoder


def synth(model, vocoder, text: str, dst: str) -> None:
    # Force a terminal period so the model has a clear stop token. Do NOT pad
    # with extra "." pause tokens: F5 fills over-allocated frames with
    # hallucinated sound, not silence. The ffmpeg apad below adds real silence.
    # Every caller gets the same pronunciation fixes (buddy's readouts don't
    # pass through computerize.py); applying them twice is harmless.
    gen_text = pronounce.apply(text)
    if not gen_text.endswith((".", "!", "?")):
        gen_text += "."
    import random
    seed_everything(random.randint(0, sys.maxsize))
    ref_file, ref_text = utils_infer.preprocess_ref_audio_text(
        str(REF_AUDIO), REF_TEXT, show_info=lambda *a, **k: None
    )
    wav, sr, _ = utils_infer.infer_process(
        ref_file, ref_text, gen_text, model, vocoder, "vocos",
        show_info=lambda *a, **k: None, speed=SPEED, device=DEVICE,
    )
    import soundfile as sf
    sf.write(dst, wav, sr)
    # Append 500 ms of real silence so a slightly clipped tail still carries
    # the last syllable through to a natural end. Not fatal if it fails.
    try:
        import shutil
        import subprocess
        tmp_pad = dst + ".pad.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", dst,
             "-af", "apad=pad_dur=0.5", "-c:a", "pcm_s16le", tmp_pad],
            check=True,
        )
        shutil.move(tmp_pad, dst)
    except Exception:
        pass


def _reply(obj: dict) -> None:
    _proto.write(json.dumps(obj) + "\n")
    _proto.flush()


def main() -> int:
    for p in (CKPT_PATH, VOCAB_PATH, REF_AUDIO):
        if not p.exists():
            _reply({"ready": False, "err": f"missing {p}"})
            return 2
    model, vocoder = build()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
    _reply({"ready": True})
    _log("ready")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        t = time.monotonic()
        try:
            req = json.loads(line)
            text = (req.get("text") or "").strip()
            if not text:
                _reply({"ok": False, "err": "empty text"})
                continue
            synth(model, vocoder, text, req["dst"])
            _reply({"ok": True})
            _log(f"synth {time.monotonic() - t:.2f}s for {len(text)} chars")
        except Exception as e:
            _reply({"ok": False, "err": str(e)})
    _log("stdin closed, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
