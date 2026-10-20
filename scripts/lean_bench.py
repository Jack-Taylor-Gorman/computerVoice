#!/usr/bin/env python3
"""Time requests against the live Majel F5 socket and report WAV stats.

Usage: lean_bench.py OUTDIR [phrase ...]
Stdlib only, so it can run under any interpreter while the daemon is live.
"""
import json, socket, sys, time, wave, array, math, os

SOCK = "/tmp/majel_f5.sock"
DEFAULT = [
    "Verification check.",
    "Working.",
    "Build complete. All tests passed.",
    "Accessing the library computer for the latest records.",
]


def ask(text, dst, timeout=180.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall(json.dumps({"text": text, "dst": dst}).encode() + b"\n")
    buf = b""
    while not buf.endswith(b"\n"):
        c = s.recv(4096)
        if not c:
            break
        buf += c
    s.close()
    return json.loads(buf.decode())


def stats(path):
    with wave.open(path) as w:
        n, sr, sw = w.getnframes(), w.getframerate(), w.getsampwidth()
        raw = w.readframes(n)
    a = array.array("h", raw) if sw == 2 else array.array("f", raw)
    rms = math.sqrt(sum(x * x for x in a) / max(len(a), 1)) / (32768 if sw == 2 else 1)
    return n / sr, rms


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    phrases = sys.argv[2:] or DEFAULT
    for i, p in enumerate(phrases):
        dst = os.path.join(out, f"{i:02d}.wav")
        t = time.monotonic()
        r = ask(p, dst)
        dt = time.monotonic() - t
        if r.get("ok"):
            d, rms = stats(dst)
            print(f"{dt:6.2f}s  dur={d:5.2f}s rms={rms:.4f}  {p!r}", flush=True)
        else:
            print(f"{dt:6.2f}s  FAIL {r}  {p!r}", flush=True)


if __name__ == "__main__":
    main()
