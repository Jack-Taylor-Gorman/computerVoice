#!/usr/bin/env python3
"""Audible demo: 3 dev-feedback inputs × {offline rewriter, API rewriter}
synthesized through the F5-TTS daemon and played back-to-back.

For each example:
  1. Print the input prose.
  2. Run computerize.py forced into OFFLINE mode → synth → paplay.
  3. Run computerize.py forced into API   mode → synth → paplay.

Speed defaults to 0.9 (10% slower) per the daemon config; nothing here
overrides it.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import computerize  # noqa: E402

SOCK = "/tmp/majel_f5.sock"

# Pull the API key (held in the user's local config — never logged).
_cfg = json.loads((Path.home() / ".majel_config.json").read_text())
_API_KEY = _cfg.get("anthropic_api_key")

INPUTS = [
    "I went through the auth flow and found a missing null check in the JWT validation. I added the check, the test suite is green, and I pushed to main.",
    "I'm not sure which config file you wanted me to update — there are three files matching that pattern across different services.",
    "Plan: refactor the worker queue to use Redis streams, then migrate existing job records, then deploy in stages with a feature flag.",
]


def force_offline():
    computerize._voice_mode = lambda: "offline"
    computerize._api_key = lambda: None


def force_api():
    computerize._voice_mode = lambda: "api"
    computerize._api_key = lambda: _API_KEY


def synth(text: str, dst: str) -> bool:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(120.0)
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


def play(path: str) -> None:
    subprocess.run(["paplay", path], check=False)


def main() -> int:
    if not os.path.exists(SOCK):
        sys.stderr.write(f"missing F5 daemon socket: {SOCK}\n")
        return 2
    if not _API_KEY:
        sys.stderr.write("no anthropic_api_key in ~/.majel_config.json\n")
        return 2

    out_dir = Path("/tmp/demo_offline_vs_api")
    out_dir.mkdir(exist_ok=True)

    print("\n══════════════════════════════════════════════════════════════")
    print("  OFFLINE rewriter  vs  API rewriter   ·   F5-TTS @ speed 0.9")
    print("══════════════════════════════════════════════════════════════")

    # Pre-synthesize labels in the same Majel voice so each cell starts
    # with an audible header — no terminal-side guessing about which is
    # offline vs API while listening.
    print("\n(generating labels…)")
    for i in range(1, len(INPUTS) + 1):
        for tag in ("Offline", "API"):
            lbl_path = out_dir / f"_label_{i}_{tag.lower()}.wav"
            if not lbl_path.exists():
                synth(f"Example {i}. {tag}.", str(lbl_path))

    for i, src in enumerate(INPUTS, 1):
        print(f"\n── EXAMPLE {i} ──")
        print(f"INPUT:    {src}")

        # OFFLINE cell — label first, then content.
        play(str(out_dir / f"_label_{i}_offline.wav"))
        force_offline()
        offline_out = computerize.computerize(src) or "(empty)"
        print(f"  ▶ OFFLINE: {offline_out}")
        offline_wav = str(out_dir / f"{i}_offline.wav")
        if synth(offline_out, offline_wav):
            play(offline_wav)
        else:
            print("    (offline synth failed)")

        # API cell — label first, then content.
        play(str(out_dir / f"_label_{i}_api.wav"))
        force_api()
        api_out = computerize.computerize(src) or "(empty)"
        print(f"  ▶ API:     {api_out}")
        api_wav = str(out_dir / f"{i}_api.wav")
        if synth(api_out, api_wav):
            play(api_wav)
        else:
            print("    (api synth failed)")

    print("\n══ DONE ══\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
