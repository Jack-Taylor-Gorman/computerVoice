#!/usr/bin/env bash
# Notification hook: fire beep 30 with minimum possible latency.
# Strategy: use pre-decoded WAV + paplay (no ffmpeg demux, no SDL init),
# kick playback in the background before anything else, then filter.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WAV="$DIR/sounds/computer/computerbeep_30.wav"

PAYLOAD="$(cat)"
LOW="$(printf '%s' "$PAYLOAD" | tr '[:upper:]' '[:lower:]')"

TS="$(date +%H:%M:%S.%3N)"
echo "$TS notify: $PAYLOAD" >> /tmp/majel_notify.log

# Use the explicit notification_type field when present. Only chime for
# permission prompts — ignore idle timers, progress events, and generic info.
case "$LOW" in
    *'"notification_type":"permission_prompt"'*)
        paplay "$WAV" >/dev/null 2>&1 &
        disown
        ;;
    *'"notification_type":"idle"'*|*'"notification_type":"info"'*)
        : # explicit ignore
        ;;
    *"permission"*|*"approve"*|*"authori"*|*"needs your"*)
        # Fallback match if notification_type field is absent in older Claude Code builds.
        paplay "$WAV" >/dev/null 2>&1 &
        disown
        ;;
esac
exit 0
