#!/usr/bin/env python3
"""Global listener: play Star Trek comm SFX when space is held in a Claude Code terminal.

- Start-transmission when space held > WARMUP_MS (matches Claude's key-repeat threshold).
- End-transmission when space released after a hold that triggered start.
- Only fires when focused X11 window title or WM_CLASS contains 'claude'.
- Skip single taps and regular typing.
"""
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from pynput import keyboard

ROOT = Path(__file__).resolve().parent
START_SFX = ROOT / "sounds" / "computer" / "communications_start_transmission.mp3"
END_SFX = ROOT / "sounds" / "computer" / "communications_end_transmission.mp3"
NAV_SFX = ROOT / "sounds" / "computer" / "computerbeep_5.wav"
WARMUP_MS = 180
MATCH = ("claude",)

_state = {"press_at": None, "started": False, "timer": None}


def focused_is_claude() -> bool:
    try:
        r = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=0.5,
        )
        name = (r.stdout or "").lower()
        if any(m in name for m in MATCH):
            return True
        wid = subprocess.run(
            ["xdotool", "getactivewindow"], capture_output=True, text=True, timeout=0.5
        ).stdout.strip()
        if wid:
            cls = subprocess.run(
                ["xprop", "-id", wid, "WM_CLASS"], capture_output=True, text=True, timeout=0.5
            ).stdout.lower()
            if any(m in cls for m in MATCH):
                return True
    except Exception:
        return False
    return False


_reaper_started = False


def _reap_children() -> None:
    """Background thread that waits on completed Popen children, preventing zombies."""
    import os as _os
    while True:
        try:
            pid, _status = _os.waitpid(-1, 0)
            if pid == 0:
                break
        except ChildProcessError:
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)


def play(path: Path) -> None:
    global _reaper_started
    if not path.exists():
        return
    wav_sibling = path.with_suffix(".wav")
    if path.suffix.lower() != ".wav" and wav_sibling.exists():
        target = wav_sibling
    else:
        target = path
    if target.suffix.lower() == ".wav":
        cmd = ["paplay", str(target)]
    else:
        # mp3 without pre-decoded sibling: decode via ffmpeg → paplay pipe.
        cmd_str = f"ffmpeg -loglevel quiet -i '{target}' -f wav - | paplay"
        subprocess.Popen(cmd_str, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not _reaper_started:
            _reaper_started = True
            threading.Thread(target=_reap_children, daemon=True).start()
        return
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not _reaper_started:
        _reaper_started = True
        threading.Thread(target=_reap_children, daemon=True).start()


def trigger_start():
    if _state["press_at"] is None:
        return
    if not focused_is_claude():
        return
    _state["started"] = True
    play(START_SFX)


def on_press(key):
    if key in (keyboard.Key.up, keyboard.Key.down):
        if focused_is_claude():
            play(NAV_SFX)
        return
    if key != keyboard.Key.space:
        return
    if _state["press_at"] is not None:
        return
    _state["press_at"] = time.monotonic()
    _state["started"] = False
    t = threading.Timer(WARMUP_MS / 1000.0, trigger_start)
    t.daemon = True
    _state["timer"] = t
    t.start()


def on_release(key):
    if key != keyboard.Key.space:
        return
    started = _state["started"]
    t = _state["timer"]
    if t:
        t.cancel()
    _state["press_at"] = None
    _state["started"] = False
    _state["timer"] = None
    if started:
        play(END_SFX)


def main() -> int:
    if os.environ.get("MAJEL_VOICE_SFX") == "0":
        return 0
    with keyboard.Listener(on_press=on_press, on_release=on_release) as l:
        l.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
