#!/usr/bin/env python3
"""Generate the showcase WAVs for web/index.html.

Each sample is the project's standard pre-voice beep
(sounds/computer/computerbeep_55.wav) + 200 ms silence + the F5-TTS
synthesis of the source text. Final encode is 24 kHz mono PCM 16-bit.

The text is run through computerize._post_process first so acronyms
("API" → "application programming interface"), version numbers, and
heteronyms get the same expansion as a production stop-hook utterance.

Usage:
    ./venv/bin/python scripts/build_web_audio.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import computerize  # noqa: E402

SOCK = "/tmp/majel_f5.sock"
BEEP = ROOT / "sounds" / "computer" / "computerbeep_55.wav"
OUT_DIR = ROOT / "web" / "audio"
SAMPLE_RATE = 24000

SAMPLES: list[tuple[str, str]] = [
    (
        "intro",
        "Computer ready. Majel voice subsystem online. Coding sessions "
        "narrated in canonical LCARS cadence. Project status reports "
        "available on request. Standing by.",
    ),
    (
        "api-setup",
        "Configuration sequence. Step one. Edit majel hyphen config dot "
        "jay son in home directory. Step two. Set anthropic application "
        "programming interface key field. Step three. Set voice mode to "
        "application programming interface. Step four. Restart F five "
        "daemon. Acknowledged.",
    ),
    (
        "multi-session",
        "Project Alpha. Test suite complete. All tests nominal. "
        "Project Beta. Pull request four five two one merged. Push to "
        "main branch complete. Project Gamma. Three candidate hosts "
        "located. Specify selection. Standing by.",
    ),
]


def synth_via_daemon(text: str, dst: str, timeout: float = 180.0) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK)
    s.sendall(json.dumps({"text": text, "dst": dst}).encode() + b"\n")
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    s.close()
    return bool(json.loads(buf.decode() or '{"ok": false}').get("ok"))


def concat(beep: Path, voice: Path, dst: Path) -> None:
    """beep + 200ms silence + voice → dst.

    ffmpeg concat needs all inputs at the same sample rate. The beep
    wav in-repo is 22.05 kHz mono; we resample everything to 24 kHz
    mono PCM 16-bit so the output drops cleanly into <audio> tags.
    """
    with tempfile.TemporaryDirectory() as td:
        td_p = Path(td)
        beep_n = td_p / "beep.wav"
        sil_n = td_p / "silence.wav"
        voice_n = td_p / "voice.wav"
        list_n = td_p / "list.txt"

        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(beep),
             "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le",
             str(beep_n)],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "lavfi", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
             "-t", "0.2", "-c:a", "pcm_s16le", str(sil_n)],
            check=True,
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(voice),
             "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le",
             str(voice_n)],
            check=True,
        )
        list_n.write_text(
            f"file '{beep_n}'\nfile '{sil_n}'\nfile '{voice_n}'\n"
        )
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-f", "concat", "-safe", "0", "-i", str(list_n),
             "-c:a", "pcm_s16le", str(dst)],
            check=True,
        )


def main() -> int:
    if not BEEP.exists():
        sys.stderr.write(f"missing beep: {BEEP}\n")
        return 2
    if not os.path.exists(SOCK):
        sys.stderr.write(f"F5 daemon socket not found: {SOCK}\n"
                         f"start it with: nohup ./venv/bin/python f5_daemon.py &\n")
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, source in SAMPLES:
        text = computerize._post_process(source)
        # Same trailing pad the daemon adds so the tail isn't clipped.
        with tempfile.TemporaryDirectory() as td:
            voice = Path(td) / "voice.wav"
            print(f"  → {name} : {text[:80]}…")
            if not synth_via_daemon(text, str(voice)):
                sys.stderr.write(f"synth failed for {name}\n")
                continue
            dst = OUT_DIR / f"{name}.wav"
            concat(BEEP, voice, dst)
            kb = dst.stat().st_size // 1024
            print(f"    wrote {dst.relative_to(ROOT)}  ({kb} KB)")

    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
