#!/usr/bin/env python3
"""Text -> edge-tts WAV -> RVC (Majel) -> play, with stock-phrase fast path.

Usage: echo "Working." | speak.py
Env: MAJEL=0 disables. MAJEL_VOICE overrides base TTS voice. MAJEL_CPU=1 forces CPU.
"""
import asyncio
import json
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

DAEMON_SOCK = "/tmp/majel.sock"          # RVC daemon (legacy backend).
F5_DAEMON_SOCK = "/tmp/majel_f5.sock"    # F5-TTS daemon (finetune backend).
BG_PID_FILE = Path(__file__).resolve().parent / ".background.pid"

# Hook children may inherit a minimal env missing XDG_RUNTIME_DIR, which paplay
# needs to locate the PulseAudio socket. Reconstruct from UID when absent.
if "XDG_RUNTIME_DIR" not in os.environ:
    os.environ["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"


def _signal_bg(sig: int) -> None:
    """Send SIGUSR1 (duck) / SIGUSR2 (restore) to background.py if running."""
    try:
        pid = int(BG_PID_FILE.read_text().strip())
        os.kill(pid, sig)
    except (OSError, ValueError):
        pass


def duck_background() -> None:
    import signal as _signal
    _signal_bg(_signal.SIGUSR1)


def restore_background() -> None:
    import signal as _signal
    _signal_bg(_signal.SIGUSR2)


def _mic_in_use() -> bool:
    """Return True if a real voice capture is active (e.g. chat STT).

    Disabled by default — most users keep chat STT armed all the time,
    which kept silencing every utterance. Opt back in by setting
    MAJEL_RESPECT_MIC=1 if you want voice to halt while dictating;
    flock-serialization already prevents Majel from talking over an
    in-flight utterance, so this is now strictly a politeness setting.
    The historical MAJEL_IGNORE_MIC=1 override is preserved for clarity
    but is no longer the way to disable the check.
    """
    if os.environ.get("MAJEL_IGNORE_MIC") == "1":
        return False
    if os.environ.get("MAJEL_RESPECT_MIC") != "1":
        return False
    try:
        r = subprocess.run(
            ["pactl", "list", "short", "source-outputs"],
            capture_output=True, text=True, timeout=2,
        )
        for line in (r.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            if "monitor" in line.lower():
                continue
            # Sample-rate filter — peak meters are <=200Hz, voice >=8kHz.
            m = re.search(r"(\d+)\s*Hz", line)
            if m and int(m.group(1)) < 8000:
                continue
            return True
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def infer_via_daemon(src: str, dst: str, timeout: float = 60.0) -> bool:
    """Send inference request to majel_daemon (RVC). Returns True on success."""
    if not os.path.exists(DAEMON_SOCK):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(DAEMON_SOCK)
        s.sendall(json.dumps({"src": src, "dst": dst}).encode() + b"\n")
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


def infer_via_f5_daemon(text: str, dst: str, timeout: float = 60.0) -> bool:
    """Synthesize via the F5-TTS daemon directly from text. Returns True on success."""
    if not os.path.exists(F5_DAEMON_SOCK):
        return False
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(F5_DAEMON_SOCK)
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

import torch as _torch
_orig_torch_load = _torch.load
def _patched_load(*a, **kw):
    kw.setdefault("weights_only", False)
    return _orig_torch_load(*a, **kw)
_torch.load = _patched_load

import edge_tts
from rvc_python.infer import RVCInference

ROOT = Path(__file__).resolve().parent
MODEL = ROOT / "Majel" / "Majel.pth"
_idx_new = ROOT / "Majel" / "added_IVF_Flat_Majel_v2.index"
_idx_old = ROOT / "Majel" / "added_IVF430_Flat_nprobe_1_Majel_v2.index"
INDEX = str(_idx_new) if _idx_new.exists() else (str(_idx_old) if _idx_old.exists() else "")
VOICE_DIR = ROOT / "sounds" / "computer" / "voice"
BASE_VOICE = os.environ.get("MAJEL_VOICE", "en-GB-SoniaNeural")
MAX_CHARS = 2000

ALERT_INFO_NEEDED = ROOT / "sounds" / "computer" / "alert03.mp3"
ALERT_PRE_VOICE = ROOT / "sounds" / "computer" / "computerbeep_55.mp3"
ALERT_AWAITING_INPUT = ROOT / "sounds" / "computer" / "computerbeep_30.mp3"
INFO_NEEDED_MARKERS = (
    "specify parameters",
    "please restate",
    "direction unclear",
    "insufficient data",
    "information not on file",
    "unable to locate",
)
AWAITING_INPUT_MARKERS = (
    "shall i", "should i", "would you like", "do you want",
    "which would you", "which one", "please confirm", "please choose",
    "please select", "please approve", "please authorize", "please authorise",
    "may i proceed", "permission to", "awaiting authorization", "awaiting authorisation",
    "awaiting input", "awaiting confirmation", "awaiting selection",
    "confirm to proceed", "approve to proceed", "proceed? ", "continue?",
    "y/n", "(yes/no)", "select an option", "make a selection",
    "specify parameters", "please restate",
)


def awaiting_input(text: str) -> bool:
    low = text.lower().strip()
    if not low:
        return False
    if any(m in low for m in AWAITING_INPUT_MARKERS):
        return True
    return low.endswith("?")

SIMPLE_ACK_BEEPS = [VOICE_DIR.parent / f"computerbeep_{i}.mp3" for i in range(68, 77)]

# Phrases that indicate a simple imperative task was just executed — no voice, just a beep.
SIMPLE_TASK_MARKERS = (
    "pushed", "push complete", "pushed to",
    "opened", "launched", "started",
    "committed", "commit created", "commit pushed",
    "merged", "pulled", "fetched", "cloned",
    "installed", "uninstalled",
    "copied", "moved", "renamed",
    "deleted", "removed",
    "saved", "created file", "file created", "file updated",
    "done.", "task complete", "complete.",
    "acknowledged",
)


def simple_task_ack(text: str) -> bool:
    # Conservative: only fire beep-only when the WHOLE response is a short
    # confirmation (<=4 words) whose text is dominated by a task-complete
    # marker. Longer responses always get voice, even if they contain a
    # marker word somewhere ("Binary installed and here's what I did next...").
    low = re.sub(r"[^a-z ]", " ", text.lower()).strip()
    if not low:
        return False
    words = low.split()
    if len(words) > 4:
        return False
    joined = " ".join(words)
    for m in SIMPLE_TASK_MARKERS:
        m_clean = re.sub(r"[^a-z ]", " ", m).strip()
        if joined == m_clean or joined.endswith(" " + m_clean) or joined.startswith(m_clean + " "):
            return True
    return False


STOCK = {
    "unable to comply": "unabletocomply.mp3",
    "acknowledged": "affirmative1_ep.mp3",
    "affirmative": "affirmative1_ep.mp3",
    "working": "accessinglibrarycomputerdata_clean.mp3",
    "please restate": "pleaserestateasinglequestion.mp3",
    "please restate the question": "pleaserestateasinglequestion.mp3",
    "please restate command": "pleaserestatecommand_ep.mp3",
    "access denied": "youarenotauthorisedtoaccessthisfacility_clean.mp3",
    "proximity alert": "proximityalert_ep.mp3",
    "transfer complete": "transfercomplete_clean.mp3",
    "diagnostic complete": "diagnosticcomplete_ep.mp3",
    "specify parameters": "specifyparameters.mp3",
    "please state command": "pleaserestatecommand_ep.mp3",
}


def stock_match(text: str) -> str | None:
    t = re.sub(r"[^a-z ]", "", text.lower()).strip()
    if not t:
        return None
    for phrase, fname in STOCK.items():
        if t == phrase or t.startswith(phrase + " ") or t.endswith(" " + phrase):
            p = VOICE_DIR / fname
            if p.exists():
                return str(p)
    return None


async def tts(text: str, out_path: str) -> None:
    ssml_safe = text.replace("&", "and").replace("<", "").replace(">", "")
    c = edge_tts.Communicate(ssml_safe, BASE_VOICE, rate="-8%", pitch="-2Hz", volume="+0%")
    await c.save(out_path)


def play(path: str) -> None:
    p = Path(path)
    wav_sibling = p.with_suffix(".wav")
    if p.suffix.lower() != ".wav" and wav_sibling.exists():
        target = str(wav_sibling)
        r = subprocess.run(["paplay", target], capture_output=True, text=True)
    elif p.suffix.lower() == ".wav":
        target = path
        r = subprocess.run(["paplay", path], capture_output=True, text=True)
    else:
        target = path
        proc = subprocess.Popen(
            ["ffmpeg", "-loglevel", "quiet", "-i", path, "-f", "wav", "-"],
            stdout=subprocess.PIPE,
        )
        r = subprocess.run(["paplay", "--raw=0"], stdin=proc.stdout, capture_output=True, text=True)
        proc.wait()
    dbg = os.environ.get("MAJEL_LOG")
    if dbg:
        with open(dbg, "a") as f:
            f.write(f"play {target} rc={r.returncode} err={(r.stderr or '').strip()[:200]}\n")


def main() -> int:
    if os.environ.get("MAJEL") == "0":
        return 0
    # GUI-controlled master switch.
    cfg_path = Path.home() / ".majel_config.json"
    if cfg_path.exists():
        try:
            import json as _json
            cfg = _json.loads(cfg_path.read_text())
            if not cfg.get("voice_enabled", True):
                return 0
        except Exception:
            pass
    text = sys.stdin.read().strip()
    if not text:
        return 0
    text = text[:MAX_CHARS]

    # Halt-during-STT: only fires when MAJEL_RESPECT_MIC=1 is set
    # (default off). Loud-fail with a low double-beep so the user knows
    # we heard them and stayed quiet — silence-is-failure was the
    # diagnosis the user kept hitting.
    if _mic_in_use():
        dbg = os.environ.get("MAJEL_LOG")
        if dbg:
            with open(dbg, "a") as f:
                f.write("skip: mic in use (likely chat STT)\n")
        try:
            low_beep = ROOT / "sounds" / "computer" / "computerbeep_30.mp3"
            if low_beep.exists():
                play(str(low_beep))
                play(str(low_beep))
        except Exception:
            pass
        return 0

    # Voice every turn — no beep-only short-circuits. The rewriter guarantees
    # something speakable (as short as a single word for trivial acks).
    info_needed = any(m in text.lower() for m in INFO_NEEDED_MARKERS) and ALERT_INFO_NEEDED.exists()
    if info_needed:
        play(str(ALERT_INFO_NEEDED))

    stock = stock_match(text)
    if stock:
        duck_background()
        try:
            if not info_needed and ALERT_PRE_VOICE.exists():
                play(str(ALERT_PRE_VOICE))
            play(stock)
        finally:
            restore_background()
        return 0

    # F5-TTS finetune backend — preferred when its daemon is up because the
    # finetune carries Majel timbre directly and skips the edge-tts → RVC
    # double-stack. Backend selection: env MAJEL_BACKEND, then config
    # 'backend' field, default "f5". Falls through to RVC on any failure.
    backend = os.environ.get("MAJEL_BACKEND", "").lower()
    if not backend and cfg_path.exists():
        try:
            import json as _json
            backend = (_json.loads(cfg_path.read_text()).get("backend") or "").lower()
        except Exception:
            pass
    if not backend:
        backend = "f5"
    if backend == "f5":
        with tempfile.TemporaryDirectory() as td:
            f5_out = os.path.join(td, "out.wav")
            if infer_via_f5_daemon(text, f5_out, timeout=120.0):
                dbg = os.environ.get("MAJEL_LOG")
                if dbg:
                    shutil.copy(f5_out, "/tmp/majel_last.wav")
                    with open(dbg, "a") as f:
                        f.write(f"f5: wrote /tmp/majel_last.wav size={os.path.getsize(f5_out)}\n")
                duck_background()
                try:
                    if not info_needed and ALERT_PRE_VOICE.exists():
                        play(str(ALERT_PRE_VOICE))
                    play(f5_out)
                finally:
                    restore_background()
                return 0
            # F5 daemon unreachable or threw — fall through to legacy
            # stack. Audible cue: alert03 (descending tone) so the user
            # knows the primary backend is down and we're attempting RVC.
            dbg = os.environ.get("MAJEL_LOG")
            if dbg:
                with open(dbg, "a") as f:
                    f.write("f5: daemon unavailable, falling back to RVC\n")
            try:
                if ALERT_INFO_NEEDED.exists():
                    play(str(ALERT_INFO_NEEDED))
            except Exception:
                pass

    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "src.mp3")
        src_wav = os.path.join(td, "src.wav")
        src_padded = os.path.join(td, "src_padded.wav")
        dst_raw = os.path.join(td, "out_raw.wav")
        dst = os.path.join(td, "out.wav")

        asyncio.run(tts(text, src))
        # Decode TTS mp3 → mono 40k wav.
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src, "-ar", "40000", "-ac", "1", src_wav],
            check=True,
        )
        # Pre-pad 400ms silence head + 200ms tail so RVC's first/last-frame artifacts
        # land in silence, not on the first/last word.
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src_wav,
             "-af", "adelay=400|400,apad=pad_dur=0.2",
             "-ar", "40000", "-ac", "1", src_padded],
            check=True,
        )

        if not infer_via_daemon(src_padded, dst_raw):
            device = "cuda:0" if os.environ.get("MAJEL_CPU") != "1" else "cpu"
            rvc = RVCInference(device=device)
            rvc.load_model(str(MODEL), index_path=INDEX, version="v2")
            index_rate = float(os.environ.get("MAJEL_INDEX_RATE", "0.5")) if INDEX else 0.0
            rvc.set_params(
                f0method="rmvpe", f0up_key=0,
                index_rate=index_rate,
                protect=0.33, filter_radius=3, rms_mix_rate=0.25,
            )
            rvc.infer_file(src_padded, dst_raw)

        # Trim the 400ms pre-pad head (leaves a 30ms fade on the first word to
        # mask any residual RVC first-frame pop) and a short fade-out at the tail.
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", dst_raw,
             "-af", "atrim=start=0.38,asetpts=PTS-STARTPTS,"
                    "afade=t=in:st=0:d=0.03,"
                    "areverse,afade=t=in:st=0:d=0.04,areverse",
             "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", dst],
            check=True,
        )

        dbg = os.environ.get("MAJEL_LOG")
        if dbg:
            keep = "/tmp/majel_last.wav"
            shutil.copy(dst, keep)
            with open(dbg, "a") as f:
                f.write(f"wrote {keep} size={os.path.getsize(keep)}\n")
        duck_background()
        try:
            if not info_needed and ALERT_PRE_VOICE.exists():
                play(str(ALERT_PRE_VOICE))
            play(dst)
        finally:
            restore_background()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        sys.stderr.write(f"speak.py error: {e}\n")
        # Triple-beep so the user audibly hears "all voice paths dead —
        # fix me" instead of pure silence + an entry in a log they will
        # never check. Loud-fail per council recommendation.
        try:
            import time as _time
            triple_beep = ROOT / "sounds" / "computer" / "computerbeep_55.mp3"
            if triple_beep.exists():
                for _ in range(3):
                    play(str(triple_beep))
                    _time.sleep(0.08)
        except Exception:
            pass
        sys.exit(1)
