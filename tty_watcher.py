#!/usr/bin/env python3
"""Tail Claude Code's captured stdout and fire beep 30 the instant the
approval menu renders on-screen — bypasses Claude's 6s Notification latency.

Paired with start_claude.sh which uses `script(1)` to fork a PTY and tee all
Claude output to /tmp/claude-stream.log while the user still sees their
normal session. This watcher tails that log, strips ANSI, pattern-matches
the approval prompt, and plays beep 30 via paplay the moment it appears.

Debounced at DEBOUNCE_S so a single prompt fires one beep even when the
terminal redraws the menu multiple times.
"""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

LOG = Path(os.environ.get("CLAUDE_STREAM_LOG", "/tmp/claude-stream.log"))
ROOT = Path(__file__).resolve().parent
WAV = ROOT / "sounds" / "computer" / "computerbeep_30.wav"
DEBOUNCE_S = 2.5

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b[=>]|\r")

APPROVAL_PATTERNS = [
    re.compile(r"Do you want to proceed\??", re.IGNORECASE),
    re.compile(r"Do you want to (make|apply|run|execute|allow)", re.IGNORECASE),
    re.compile(r"Would you like to (proceed|continue|run)", re.IGNORECASE),
    re.compile(r"\b1\.\s*Yes\b.*\b2\.\s*No\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"❯\s*1\.\s*Yes", re.IGNORECASE),
    re.compile(r"\bpermission to use\b", re.IGNORECASE),
    re.compile(r"\brequires your approval\b", re.IGNORECASE),
    re.compile(r"\bwaiting for your input\b", re.IGNORECASE),
]


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub("", s)


def beep() -> None:
    if not WAV.exists():
        return
    subprocess.Popen(
        ["paplay", str(WAV)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def tail(path: Path):
    """Generator yielding newly-appended chunks from path. Reopens on truncate."""
    while not path.exists():
        time.sleep(0.2)
    f = path.open("rb")
    f.seek(0, os.SEEK_END)
    inode = os.fstat(f.fileno()).st_ino
    buf = b""
    while True:
        chunk = f.read(4096)
        if chunk:
            buf += chunk
            # Yield on any terminal-redraw chunk; ANSI cursor moves aren't line-based.
            yield buf.decode("utf-8", errors="replace")
            buf = b""
            continue
        time.sleep(0.05)
        try:
            st = os.stat(path)
            if st.st_ino != inode or st.st_size < f.tell():
                f.close()
                f = path.open("rb")
                inode = os.fstat(f.fileno()).st_ino
        except FileNotFoundError:
            time.sleep(0.5)


def main() -> int:
    last_fire = 0.0
    window = ""  # rolling tail-of-output buffer for multi-chunk pattern matches
    for chunk in tail(LOG):
        window = (window + strip_ansi(chunk))[-2000:]
        if any(p.search(window) for p in APPROVAL_PATTERNS):
            now = time.monotonic()
            if now - last_fire > DEBOUNCE_S:
                beep()
                last_fire = now
                window = ""  # flush so the same prompt doesn't retrigger
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
