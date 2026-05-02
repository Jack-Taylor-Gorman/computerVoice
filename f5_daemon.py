#!/usr/bin/env python3
"""Long-lived F5-TTS inference daemon for the Majel finetune.

Loads the F5TTS_v1_Base + Majel finetune checkpoint + vocoder once,
then serves newline-delimited JSON requests over a Unix socket so
speak.py doesn't pay the ~10–15s model-load cost per utterance.

Protocol:
  request  : {"text": "Working.", "dst": "/tmp/foo.wav"}\\n
  response : {"ok": true}\\n        or  {"ok": false, "err": "..."}\\n

Env:
  MAJEL_F5_RUN     finetune run name (default: majel_all_run2)
  MAJEL_F5_CKPT    checkpoint stem inside that run (default: model_600)
  MAJEL_F5_REF     filename inside dataset/clips_curated/ to use as ref clip
  MAJEL_F5_REFTEXT transcript of that ref clip
  MAJEL_CPU=1      force CPU
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path

import torch as _torch
_orig_torch_load = _torch.load
def _patched_load(*a, **kw):
    kw.setdefault("weights_only", False)
    return _orig_torch_load(*a, **kw)
_torch.load = _patched_load

from f5_tts.api import F5TTS

ROOT = Path(__file__).resolve().parent
SOCK_PATH = "/tmp/majel_f5.sock"
LOCK_PATH = "/tmp/majel_f5_daemon.lock"

RUN = os.environ.get("MAJEL_F5_RUN", "majel_all_run2")
CKPT_NAME = os.environ.get("MAJEL_F5_CKPT", "model_600")
CKPT_PATH = ROOT / "venv" / "lib" / "python3.10" / "ckpts" / RUN / f"{CKPT_NAME}.pt"
VOCAB_PATH = ROOT / "venv" / "lib" / "python3.10" / "data" / f"{RUN}_pinyin" / "vocab.txt"
REF_NAME = os.environ.get(
    "MAJEL_F5_REF",
    "001__0.985__2.1s__Accessing_Library_Computer_Data.wav",
)
REF_AUDIO = ROOT / "dataset" / "clips_curated" / REF_NAME
REF_TEXT = os.environ.get("MAJEL_F5_REFTEXT", "Accessing Library Computer Data")

# Global speed multiplier passed to F5TTS.infer(). 0.9 = 10% slower, which
# adds a touch of LCARS "deliberate" cadence without dragging the audio.
SPEED = float(os.environ.get("MAJEL_F5_SPEED", "0.7"))

# Inference is heavyweight + holds the GPU; serialize.
_lock = threading.Lock()


def build_f5() -> F5TTS:
    return F5TTS(
        model="F5TTS_v1_Base",
        ckpt_file=str(CKPT_PATH),
        vocab_file=str(VOCAB_PATH),
        device="cuda" if os.environ.get("MAJEL_CPU") != "1" else "cpu",
    )


def handle(conn: socket.socket, f5: F5TTS) -> None:
    try:
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        if not data:
            return
        req = json.loads(data.decode())
        text = (req.get("text") or "").strip()
        dst = req["dst"]
        if not text:
            conn.sendall(b'{"ok": false, "err": "empty text"}\n')
            return
        with _lock:
            # remove_silence=True over-trims one-word utterances ("Example.")
            # so the actual word gets cut. Leave the natural tail silence in
            # place — paplay handles it fine and short clips stay intact.
            f5.infer(
                ref_file=str(REF_AUDIO),
                ref_text=REF_TEXT,
                gen_text=text,
                file_wave=dst,
                show_info=lambda *a, **k: None,
                remove_silence=False,
                speed=SPEED,
            )
        conn.sendall(b'{"ok": true}\n')
    except Exception as e:
        try:
            conn.sendall(json.dumps({"ok": False, "err": str(e)}).encode() + b"\n")
        except OSError:
            pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main() -> int:
    import fcntl
    lf = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.stderr.write("majel_f5_daemon already running\n")
        return 0

    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass

    if not CKPT_PATH.exists():
        sys.stderr.write(f"missing checkpoint: {CKPT_PATH}\n")
        return 2
    if not VOCAB_PATH.exists():
        sys.stderr.write(f"missing vocab: {VOCAB_PATH}\n")
        return 2
    if not REF_AUDIO.exists():
        sys.stderr.write(f"missing ref audio: {REF_AUDIO}\n")
        return 2

    sys.stderr.write(f"majel_f5_daemon: loading {RUN}/{CKPT_NAME}...\n")
    sys.stderr.flush()
    f5 = build_f5()
    sys.stderr.write("majel_f5_daemon: ready\n")
    sys.stderr.flush()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chmod(SOCK_PATH, 0o600)
    srv.listen(4)

    while True:
        try:
            conn, _ = srv.accept()
        except KeyboardInterrupt:
            break
        t = threading.Thread(target=handle, args=(conn, f5), daemon=True)
        t.start()

    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
