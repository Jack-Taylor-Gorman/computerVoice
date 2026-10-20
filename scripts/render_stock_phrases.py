#!/usr/bin/env python3
"""Pre-render Severian stock phrases through the F5-TTS daemon and write
them into `sounds/severian/voice/`. Once these files exist, `speak.py`'s
STOCK dict short-circuits the daemon round-trip for the most common
utterances ("I have heard you.", "It is done.", etc.) which makes
acknowledgement turns ~ instantaneous.

Run AFTER the F5 finetune is trained and `f5_daemon.py` is up with the new
checkpoint loaded. The phrases here mirror the STOCK dict in `speak.py`;
keep the two in sync if you add new fast-path entries.

Usage:
    ./scripts/render_stock_phrases.py [--force]

The daemon must be reachable at /tmp/severian_f5.sock (preferred) or the
legacy /tmp/majel_f5.sock alias.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "sounds" / "severian" / "voice"
SOCK_CANDIDATES = ("/tmp/severian_f5.sock", "/tmp/majel_f5.sock")


# (filename, text). Filename should match speak.py:STOCK values.
STOCK_PHRASES: list[tuple[str, str]] = [
    ("i_have_heard_you.wav",     "I have heard you."),
    ("i_cannot.wav",             "I cannot."),
    ("the_way_is_closed.wav",    "The way is closed."),
    ("it_is_so.wav",             "It is so."),
    ("i_think_not.wav",          "I think not."),
    ("say_again.wav",            "Say again."),
    ("i_attend.wav",             "I attend."),
    ("a_moment.wav",             "A moment."),
    ("it_is_done.wav",           "It is done."),
    ("all_is_well.wav",          "All is well."),
    ("i_await_your_word.wav",    "I await your word."),
    ("your_meaning_is_dark.wav", "Your meaning is dark to me."),
    ("not_in_my_memory.wav",     "It is not in my memory."),
    # A few more useful ones the rewriter emits frequently.
    ("the_work_is_done.wav",     "The work is done."),
    ("be_wary.wav",              "Be wary."),
    ("heed_me.wav",              "Heed me."),
    ("mark_this.wav",            "Mark this."),
    ("name_yourself.wav",        "Name yourself."),
    ("i_seek.wav",               "I seek."),
    ("i_begin.wav",              "I begin."),
]


def find_sock() -> str | None:
    for s in SOCK_CANDIDATES:
        if os.path.exists(s):
            return s
    return None


def synth(text: str, dst: str, sock: str, timeout: float = 120.0) -> bool:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock)
        s.sendall(json.dumps({"text": text, "dst": dst}).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        resp = json.loads(buf.decode() or '{"ok": false}')
        return bool(resp.get("ok"))
    except (OSError, json.JSONDecodeError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if the wav already exists")
    args = ap.parse_args()

    sock = find_sock()
    if not sock:
        print("F5 daemon socket not found at any of:", file=sys.stderr)
        for s in SOCK_CANDIDATES:
            print(f"  {s}", file=sys.stderr)
        print("Start the daemon first: bash run_f5_daemon.sh &", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"daemon at {sock} → writing to {OUT_DIR}")
    n_done = 0
    n_skipped = 0
    n_failed = 0
    for fname, text in STOCK_PHRASES:
        dst = OUT_DIR / fname
        if dst.exists() and not args.force:
            print(f"  skip   {fname:30s} (already present)")
            n_skipped += 1
            continue
        ok = synth(text, str(dst), sock)
        if ok and dst.exists() and dst.stat().st_size > 0:
            print(f"  wrote  {fname:30s} ({text!r})")
            n_done += 1
        else:
            print(f"  FAILED {fname:30s} ({text!r})", file=sys.stderr)
            n_failed += 1

    print(f"\nrendered: {n_done}  skipped: {n_skipped}  failed: {n_failed}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
