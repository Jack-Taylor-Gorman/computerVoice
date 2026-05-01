#!/usr/bin/env python3
"""Long-lived RVC inference daemon.

Loads the Majel RVC model once, serves newline-delimited JSON requests over a
Unix socket. speak.py becomes a thin client that sends {"src": "...", "dst": "..."}
and gets back {"ok": true} when the file is written.

Protocol:
  request  : {"src": "/tmp/foo.wav", "dst": "/tmp/bar.wav"}\n
  response : {"ok": true}\n        or  {"ok": false, "err": "..."}\n

Env:
  MAJEL_CPU=1      force CPU
  MAJEL_INDEX_RATE override default 0.5
  MAJEL_F0METHOD   override rmvpe (harvest|crepe|rmvpe)
"""
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

from rvc_python.infer import RVCInference

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / "Majel" / "Majel.pth"
_idx_new = ROOT / "Majel" / "added_IVF_Flat_Majel_v2.index"
_idx_old = ROOT / "Majel" / "added_IVF430_Flat_nprobe_1_Majel_v2.index"
INDEX = str(_idx_new) if _idx_new.exists() else (str(_idx_old) if _idx_old.exists() else "")
SOCK_PATH = "/tmp/majel.sock"
LOCK_PATH = "/tmp/majel_daemon.lock"

_lock = threading.Lock()  # serialize inference — one call at a time


def build_rvc():
    device = "cuda:0" if os.environ.get("MAJEL_CPU") != "1" else "cpu"
    r = RVCInference(device=device)
    r.load_model(str(MODEL), index_path=INDEX, version="v2")
    r.set_params(
        f0method=os.environ.get("MAJEL_F0METHOD", "rmvpe"),
        f0up_key=0,
        index_rate=float(os.environ.get("MAJEL_INDEX_RATE", "0.5")) if INDEX else 0.0,
        protect=0.33, filter_radius=3, rms_mix_rate=0.25,
    )
    return r


def handle(conn: socket.socket, rvc) -> None:
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
        src, dst = req["src"], req["dst"]
        with _lock:
            rvc.infer_file(src, dst)
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
    # Single-instance lock.
    import fcntl
    lf = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.stderr.write("majel_daemon already running\n")
        return 0

    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass

    sys.stderr.write("majel_daemon: loading RVC...\n")
    sys.stderr.flush()
    rvc = build_rvc()
    sys.stderr.write("majel_daemon: ready\n")
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
        t = threading.Thread(target=handle, args=(conn, rvc), daemon=True)
        t.start()

    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
