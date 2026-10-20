#!/usr/bin/env python3
"""Always-on front for the Majel F5-TTS voice server (stdlib only, a few MB).

Owns /tmp/majel_f5.sock so speak.py and buddy always find a socket, starts
the heavy model process (f5_worker.py) on the first request, forwards
requests to it, and stops it after MAJEL_F5_IDLE_S seconds of silence so
its ~2-3 GB of RAM and ~0.9 GB of VRAM go back to the machine. A cold start
costs a few seconds on the first utterance after idle; warm requests pay
nothing extra.

Protocol (unchanged from the single-process daemon):
  request  : {"text": "Working.", "dst": "/tmp/foo.wav"}\\n
  response : {"ok": true}\\n        or  {"ok": false, "err": "..."}\\n
Extra request, for callers that know speech is coming soon:
  request  : {"warm": true}\\n   ->  {"ok": true}\\n once the model is loaded

Env:
  MAJEL_F5_IDLE_S   seconds of silence before the worker is stopped (default
                    600; 0 keeps it loaded forever)
  MAJEL_F5_PREWARM  1 = load the model at start-up instead of on first use
  Everything else (MAJEL_F5_RUN, MAJEL_F5_CKPT, MAJEL_F5_REF, ...) is passed
  through to f5_worker.py.
"""
from __future__ import annotations

import fcntl
import json
import os
import select
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOCK_PATH = "/tmp/majel_f5.sock"
LOCK_PATH = "/tmp/majel_f5_daemon.lock"
WORKER = ROOT / "f5_worker.py"

IDLE_S = float(os.environ.get("MAJEL_F5_IDLE_S", "600"))
PREWARM = os.environ.get("MAJEL_F5_PREWARM") == "1"
LOAD_TIMEOUT_S = 180.0
REQUEST_TIMEOUT_S = 150.0


def log(msg: str) -> None:
    sys.stderr.write(f"{time.strftime('%H:%M:%S')} majel_f5_front: {msg}\n")
    sys.stderr.flush()


class Worker:
    """The model process, started lazily and stopped when idle."""

    def __init__(self) -> None:
        self.lock = threading.Lock()  # serialises start/requests/stop
        self.proc: subprocess.Popen | None = None
        self.buf = b""
        self.last_used = time.monotonic()

    def _readline(self, timeout: float) -> bytes | None:
        """One line from the worker's stdout, or None on EOF/timeout."""
        assert self.proc is not None
        fd = self.proc.stdout.fileno()
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buf:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            r, _, _ = select.select([fd], [], [], left)
            if not r:
                continue
            chunk = os.read(fd, 65536)
            if not chunk:
                return None
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line

    def _alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _start(self) -> str | None:
        """Launch the worker and wait for it to load; returns an error or None."""
        t = time.monotonic()
        env = dict(os.environ)
        env.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        self.buf = b""
        # stderr is inherited, so the worker's output lands in the same log.
        self.proc = subprocess.Popen(
            [sys.executable, str(WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            bufsize=0, cwd=str(ROOT), env=env,
        )
        log(f"worker pid={self.proc.pid} starting")
        line = self._readline(LOAD_TIMEOUT_S)
        try:
            msg = json.loads(line) if line else {}
        except ValueError:
            msg = {}
        if not msg.get("ready"):
            err = msg.get("err") or "worker failed to load (see /tmp/majel_f5_daemon.log)"
            log(f"worker start failed: {err}")
            self._kill()
            return err
        log(f"worker ready in {time.monotonic() - t:.2f}s")
        return None

    def _kill(self) -> None:
        p, self.proc = self.proc, None
        if p is None:
            return
        try:
            p.stdin.close()  # a clean EOF lets the worker exit by itself
        except OSError:
            pass
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        log(f"worker pid={p.pid} stopped rc={p.returncode}")

    def request(self, line: bytes) -> bytes:
        with self.lock:
            if not self._alive():
                if self.proc is not None:
                    self._kill()
                err = self._start()
                if err:
                    return json.dumps({"ok": False, "err": err}).encode()
            self.last_used = time.monotonic()
            try:
                self.proc.stdin.write(line.rstrip(b"\n") + b"\n")
                reply = self._readline(REQUEST_TIMEOUT_S)
            except OSError:
                reply = None
            self.last_used = time.monotonic()
            if reply is None:
                log("worker died or hung mid-request; restarting on next request")
                self._kill()
                return b'{"ok": false, "err": "voice worker died mid-request"}'
            return reply

    def warm(self) -> bytes:
        with self.lock:
            self.last_used = time.monotonic()
            if self._alive():
                return b'{"ok": true}'
            if self.proc is not None:
                self._kill()
            err = self._start()
            self.last_used = time.monotonic()
            return json.dumps({"ok": False, "err": err} if err else {"ok": True}).encode()

    def reap_if_idle(self) -> None:
        if IDLE_S <= 0 or not self.lock.acquire(blocking=False):
            return  # busy means not idle
        try:
            if self.proc is not None and not self._alive():
                log(f"worker pid={self.proc.pid} exited unexpectedly rc={self.proc.returncode}")
                self._kill()
            elif self._alive() and time.monotonic() - self.last_used > IDLE_S:
                log(f"idle for {IDLE_S:.0f}s, unloading model")
                self._kill()
        finally:
            self.lock.release()



def handle(conn: socket.socket, worker: Worker) -> None:
    try:
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        if not data.strip():
            return
        try:
            req = json.loads(data.decode())
        except ValueError as e:
            conn.sendall(json.dumps({"ok": False, "err": f"bad request: {e}"}).encode() + b"\n")
            return
        if req.get("warm"):
            reply = worker.warm()
        elif not (req.get("text") or "").strip():
            reply = b'{"ok": false, "err": "empty text"}'
        else:
            reply = worker.request(data)
        conn.sendall(reply + b"\n")
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
    lf = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.stderr.write("majel_f5_daemon already running\n")
        return 0

    if not WORKER.exists():
        sys.stderr.write(f"missing worker: {WORKER}\n")
        return 2

    try:
        os.unlink(SOCK_PATH)
    except FileNotFoundError:
        pass

    worker = Worker()

    def _shutdown(signum, _frame):
        log(f"signal {signum}, stopping")
        # Kill directly rather than via the request lock: the watchdog
        # follows its SIGTERM with SIGKILL one second later.
        p = worker.proc
        if p is not None and p.poll() is None:
            p.kill()
        try:
            os.unlink(SOCK_PATH)
        except FileNotFoundError:
            pass
        os._exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    os.chmod(SOCK_PATH, 0o600)
    srv.listen(8)
    log(f"listening on {SOCK_PATH} (idle unload after {IDLE_S:.0f}s, prewarm={PREWARM})")
    sys.stderr.write("majel_f5_daemon: ready\n")
    sys.stderr.flush()

    def _reaper():
        while True:
            time.sleep(5)
            worker.reap_if_idle()

    threading.Thread(target=_reaper, daemon=True).start()
    if PREWARM:
        threading.Thread(target=worker.warm, daemon=True).start()

    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn, worker), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
