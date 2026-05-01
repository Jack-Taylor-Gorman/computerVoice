# Majel Voice Modes — Offline vs API

The voice rewriter `computerize.py` has two modes selectable via the
**Majel Control** GUI ("VOICE MODE" panel) or directly in `~/.majel_config.json`.

## Mode 1 — Offline (default)

- 100% local. Zero network calls.
- Pipeline: regex template archetypes → dictionary substitutions → guarded fallback strip.
- Strengths: zero latency, deterministic, works without internet, no API cost.
- Weaknesses: rewrites are stale relative to current actions; long prose mostly passes through verbatim because the template guard refuses to fire on substring matches.
- Configure: `{"voice_mode": "offline"}` in `~/.majel_config.json` (or no config at all).

## Mode 2 — Claude API (context-aware)

- Calls Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for every voice utterance.
- Pipeline: full system prompt with all 15 invariants → full user prose → terse computer-speak rewrite, optionally followed by a follow-up question ("Specify selection." / "State target system." / etc.) when the prose hints at unfinished work.
- Strengths: faithful summarization, preserves the actual subject of the action, drives the work forward by asking pointed questions.
- Weaknesses: ~300-700 ms added latency per utterance; uses API tokens (~50-200 in / 30-80 out per turn).
- Activate via the GUI:
  1. `python majel_gui.py` (or double-click **Majel Control** on Desktop).
  2. In the **VOICE MODE** panel, select **CLAUDE API • CONTEXT-AWARE**.
  3. Paste an Anthropic API key (`sk-ant-…`) into the **API KEY** field.
  4. Click **TEST** — status should read "key valid".
  5. Click **SAVE**. Future Stop-hook fires use the API rewriter immediately; no relaunch needed.

## Config file

`~/.majel_config.json`:
```json
{
  "voice_enabled": true,
  "voice_mode": "api",
  "anthropic_api_key": "sk-ant-...",
  "bg_enabled": true,
  "bg_volume": 25,
  "duck_volume": 6
}
```

## Switching modes

The GUI radio writes the new value to `~/.majel_config.json` immediately. The hook reads the config on every fire, so the next voice utterance picks up the new mode without restarting any daemon.

## Falls back gracefully

If `voice_mode == "api"` but the key is missing or the API call errors, `computerize.py` silently falls back to the offline pipeline. You will hear voice output in either case — the only difference is whether it is template-quality or API-quality.

## API key precedence

`computerize.py` prefers `ANTHROPIC_API_KEY` env var if set, otherwise reads `anthropic_api_key` from the JSON config. Keep the GUI as the canonical store; the env var is a developer override.
