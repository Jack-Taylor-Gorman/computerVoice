#!/usr/bin/env python3
"""Ambient background loop: pick one location group, cycle its tracks forever.

- Groups = tracks sharing a base name (trailing _<digit> stripped).
- One group chosen per session; never mixes types.
- PID-file-locked in session_start.sh so only one instance runs.
- Plays at low volume under Majel voice via ffplay with -volume.
"""
import os
import random
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BG_DIR = ROOT / "sounds" / "background"
_raw_vol = int(os.environ.get("MAJEL_BG_VOLUME", "25"))
VOLUME = max(0, min(100, _raw_vol))
EXCLUDE_GROUPS = {"ds9_promenade"}

_current: subprocess.Popen | None = None
_ducked = False
DUCK_VOLUME = 6  # % of normal while voice plays


def _sink_input_id(pid: int) -> str | None:
    """Look up PulseAudio sink-input ID for our ffplay child."""
    try:
        r = subprocess.run(["pactl", "list", "sink-inputs"], capture_output=True, text=True, timeout=1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    block_id = None
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("Sink Input #"):
            block_id = line.split("#", 1)[1]
        elif line.startswith("application.process.id") and str(pid) in line:
            return block_id
    return None


def _set_volume(pct: int) -> None:
    if _current is None:
        return
    sid = _sink_input_id(_current.pid)
    if not sid:
        return
    subprocess.run(
        ["pactl", "set-sink-input-volume", sid, f"{pct}%"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1,
    )


def _sig_duck(_signum, _frame):
    global _ducked
    _ducked = True
    _set_volume(DUCK_VOLUME)


def _sig_restore(_signum, _frame):
    global _ducked
    _ducked = False
    _set_volume(VOLUME)


def _sigterm(_signum, _frame):
    if _current is not None:
        try:
            _current.terminate()
        except Exception:
            pass
    sys.exit(0)


signal.signal(signal.SIGTERM, _sigterm)
signal.signal(signal.SIGINT, _sigterm)
signal.signal(signal.SIGUSR1, _sig_duck)
signal.signal(signal.SIGUSR2, _sig_restore)


def groups() -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for p in sorted(BG_DIR.glob("*.mp3")):
        if p.name.startswith("tos_"):
            continue
        base = re.sub(r"_\d+$", "", p.stem)
        if base in EXCLUDE_GROUPS:
            continue
        out.setdefault(base, []).append(p)
    return out


def main() -> int:
    if os.environ.get("MAJEL_BG") == "0":
        return 0
    g = groups()
    if not g:
        return 0
    forced = os.environ.get("MAJEL_BG_GROUP", "").strip()
    if forced and forced in g:
        key = forced
    else:
        key = random.choice(list(g.keys()))
    tracks = list(g[key])
    # Playback mode: sequence (default), loop (single track repeated), random (shuffle).
    mode = os.environ.get("MAJEL_BG_MODE", "sequence").strip().lower()
    if mode == "loop":
        # Pick first track and loop it.
        tracks = tracks[:1]
    elif mode == "random":
        random.shuffle(tracks)
    sys.stderr.write(
        f"background: group={key} tracks={len(tracks)} mode={mode}\n")
    sys.stderr.flush()
    global _current
    i = 0
    while True:
        track = tracks[i % len(tracks)]
        i += 1
        # In random mode, reshuffle when we cycle back so order changes each lap.
        if mode == "random" and i % len(tracks) == 0 and len(tracks) > 1:
            random.shuffle(tracks)
        try:
            start_vol = DUCK_VOLUME if _ducked else VOLUME
            _current = subprocess.Popen(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
                 "-volume", str(start_vol), str(track)],
            )
            rc = _current.wait()
            _current = None
        except FileNotFoundError:
            sys.stderr.write("background: ffplay not found; exiting.\n")
            return 1
        if rc not in (0, None):
            time.sleep(1)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
