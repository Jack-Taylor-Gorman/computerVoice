# computerVoice

The Star Trek computer for Claude Code. When a Claude Code session finishes a
reply, Majel (an F5-TTS voice finetuned on the TNG computer) speaks a short
synopsis of it, with TNG sound effects for push-to-talk and navigation.

- **Voice:** an F5-TTS finetune served by `f5_daemon.py` on `/tmp/majel_f5.sock`.
  The model loads on the first request and unloads after ten idle minutes,
  so at rest it uses about 12 MB of RAM and no GPU memory.
- **Rewriter:** `computerize.py` turns Claude's reply into a Majel-style line,
  either offline (templates) or through the API; see `MAJEL_VOICE_MODES.md`.
- **Pronunciation:** `pronounce.py` speaks dots in names ("buddy dot pie"),
  splits run-together words and fixes vowels F5 gets wrong. Add your own
  respellings in `~/.majel_pronunciations.json`.
- **Control panel:** `majel_gui.py` ("Majel Control").

## Requirements

Linux with X11, an NVIDIA GPU with at least 4 GB of memory, Python 3.10,
PipeWire or PulseAudio (`paplay`), `ffmpeg` and `xdotool`, and Claude Code.

## Install

```bash
git clone https://github.com/Jack-Taylor-Gorman/computerVoice.git
cd computerVoice
python3.10 -m venv venv
venv/bin/pip install -r requirements.txt
scripts/fetch_assets.sh assets-v2      # voice model + sound effects, ~1 GB
```

The voice model and sound effects are too large for git, so they're
published as a [GitHub Release](https://github.com/Jack-Taylor-Gorman/computerVoice/releases)
and `fetch_assets.sh` downloads, checksums and unpacks them. The F5 vocoder
(Vocos) downloads itself from Hugging Face the first time Majel speaks.

Then hook it into Claude Code by adding these to `~/.claude/settings.json`
(use your clone's absolute path):

```json
{
  "hooks": {
    "Stop":             [{"hooks": [{"type": "command", "command": "/path/to/computerVoice/hook.sh"}]}],
    "SessionStart":     [{"hooks": [{"type": "command", "command": "/path/to/computerVoice/session_start.sh"}]}],
    "SessionEnd":       [{"hooks": [{"type": "command", "command": "/path/to/computerVoice/session_end.sh"}]}],
    "Notification":     [{"hooks": [{"type": "command", "command": "/path/to/computerVoice/notify.sh"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/path/to/computerVoice/user_prompt.sh"}]}]
  }
}
```

`session_start.sh` starts the voice server, the sound-effect listener and the
background ambience the first time a session opens.

## Publishing new assets

`scripts/release_assets.sh assets-vN` packs the current voice model, the
legacy RVC model and `sounds/` into a new release, with SHA-256 checksums.

## Disclaimer

A personal fan project. Not affiliated with or endorsed by Paramount or the
Roddenberry estate. Star Trek and its sound effects belong to their owners.
The voice model is provided for personal, non-commercial use.
