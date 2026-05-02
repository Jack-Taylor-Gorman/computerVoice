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
DUCK_VOLUME = 6  # % of normal while voice plays — overridden per-event from config


def _read_duck_floor() -> int:
    """Read ~/.majel_config.json's duck_volume (the residual %, NOT the
    cut). Re-read on every duck event so the GUI slider live-updates the
    behavior with no daemon restart needed."""
    try:
        import json as _j, os as _o
        p = _o.path.expanduser("~/.majel_config.json")
        cfg = _j.loads(open(p).read())
        return max(0, min(100, int(cfg.get("duck_volume", 6))))
    except Exception:
        return 6


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
    global _ducked, DUCK_VOLUME
    DUCK_VOLUME = _read_duck_floor()  # live-read so GUI slider takes effect
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


# How many seconds of overlap between consecutive tracks (loop and sequence
# modes both use this). Long-form ambient beds tolerate longer fades; the
# fade is auto-shrunk for tracks shorter than 4× the crossfade duration.
CROSSFADE_S = 4.0


def _track_duration(p: Path) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nokey=1:noprint_wrappers=1", str(p)],
            capture_output=True, text=True, timeout=5,
        )
        return float((r.stdout or "0").strip())
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return 0.0


def _spawn_ffplay(track: Path, vol: int, fade_in: float,
                  fade_out: float, duration: float) -> subprocess.Popen:
    """Start an ffplay child for `track` with optional fade envelopes.
    Both fades are applied via -af afade so the audio is faded at the
    decoder; ducking via pactl set-sink-input-volume is orthogonal."""
    af_chain: list[str] = []
    if fade_in > 0:
        af_chain.append(f"afade=t=in:st=0:d={fade_in}")
    if fade_out > 0 and duration > fade_out:
        af_chain.append(f"afade=t=out:st={max(0, duration - fade_out):.3f}:d={fade_out}")
    cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
           "-volume", str(vol)]
    if af_chain:
        cmd += ["-af", ",".join(af_chain)]
    cmd.append(str(track))
    return subprocess.Popen(cmd)


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
    prev_proc: subprocess.Popen | None = None
    while True:
        track = tracks[i % len(tracks)]
        i += 1
        if mode == "random" and i % len(tracks) == 0 and len(tracks) > 1:
            random.shuffle(tracks)

        duration = _track_duration(track)
        # Crossfade window — full CROSSFADE_S unless the track is too
        # short, in which case quarter-track to keep the audible content.
        cf = min(CROSSFADE_S, max(0.0, duration / 4.0)) if duration > 0 else 0.0
        # Fade-IN only when a prior track is still playing (i.e. we have
        # something to overlap with). For the very first track in a
        # session that's nothing — start clean.
        fade_in = cf if (prev_proc is not None and prev_proc.poll() is None) else 0.0
        fade_out = cf

        start_vol = DUCK_VOLUME if _ducked else VOLUME
        try:
            new_proc = _spawn_ffplay(track, start_vol, fade_in, fade_out, duration)
        except FileNotFoundError:
            sys.stderr.write("background: ffplay not found; exiting.\n")
            return 1
        _current = new_proc

        if duration <= 0:
            # Couldn't probe duration — fall back to old behavior (wait
            # for ffplay to exit) so we don't busy-loop or skip a track.
            new_proc.wait()
            prev_proc = None
            continue

        # Sleep until it's time to start the next track. The current
        # track's last `cf` seconds will overlap with the next track,
        # producing the crossfade. Skip the overlap if duration < 2*cf.
        overlap_at = max(0.0, duration - cf)
        # Sleep in small slices so SIGTERM / SIGINT remain responsive.
        slept = 0.0
        slice_s = 0.5
        while slept < overlap_at:
            time.sleep(min(slice_s, overlap_at - slept))
            slept += slice_s
            if new_proc.poll() is not None:
                # ffplay died early — break to next iteration with a
                # short pause so we don't tight-loop on a bad file.
                time.sleep(0.5)
                break
        prev_proc = new_proc
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
