#!/usr/bin/env python3
"""Five fresh dev-feedback inputs synthesized through the API rewriter
and played back-to-back. Each cell is preceded by a "Example N." Majel
header so it's clear which is which while listening.

Acronym + version + build-number expansion is applied automatically by
computerize._post_process. Speed comes from f5_daemon's SPEED setting.
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
_API_KEY = json.loads((Path.home() / ".majel_config.json").read_text()).get("anthropic_api_key")

# Force API mode regardless of config.
computerize._voice_mode = lambda: "api"
computerize._api_key = lambda: _API_KEY

INPUTS = [
    "I tracked down the regression — turns out the new caching layer was returning stale JWTs after the auth rotation. Invalidated the cache on rotation and added a test. PR 4521 is up for review.",
    "The CI build is failing on the macOS runner only. Looks like the Python 3.12 install path changed in the latest GitHub Actions image. I bumped the setup-python action to v5 and pinned 3.12.4 explicitly.",
    "Migration plan for the v2.3.0 release: first drop the deprecated /v1 API endpoints, then run the schema migration, then enable the new feature flag for 5% of traffic.",
    "Build 9876543 deployed to staging successfully. Latency p95 is up 18ms compared to the previous build — investigating the new gRPC interceptor.",
    "I'm not sure which database to point the new worker at — there are three candidate hosts in the config and the README is out of date. Could you confirm which one is the primary?",
]


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


def play(p: str) -> None:
    subprocess.run(["paplay", p], check=False)


def main() -> int:
    if not os.path.exists(SOCK):
        sys.stderr.write(f"missing F5 daemon socket: {SOCK}\n")
        return 2
    if not _API_KEY:
        sys.stderr.write("no anthropic_api_key in ~/.majel_config.json\n")
        return 2

    out_dir = Path("/tmp/demo_api_5")
    out_dir.mkdir(exist_ok=True)

    print("\n══════════════════════════════════════════════════════════════")
    print("  5 API-rewrite examples · F5-TTS · current daemon SPEED setting")
    print("══════════════════════════════════════════════════════════════")
    print("(generating labels…)")
    for i in range(1, len(INPUTS) + 1):
        lbl = out_dir / f"_label_{i}.wav"
        if not lbl.exists():
            synth(f"Example {i}.", str(lbl))

    for i, src in enumerate(INPUTS, 1):
        print(f"\n── EXAMPLE {i} ──")
        print(f"INPUT : {src}")
        out = computerize.computerize(src) or "(empty)"
        print(f"  ▶ : {out}")
        play(str(out_dir / f"_label_{i}.wav"))
        wav = str(out_dir / f"{i}.wav")
        if synth(out, wav):
            play(wav)
        else:
            print("    (synth failed)")
    print("\n══ DONE ══\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
