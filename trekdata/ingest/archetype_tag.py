"""Auto-suggest archetype from transcript using computerize.py's regex templates as priors."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from computerize import TEMPLATES  # noqa: E402

from trekdata.archetypes import BY_KEY

_PATTERN_TO_ARCHETYPE = {
    "unable to comply": "refusal",
    "access denied": "auth_gate",
    "not authorized": "auth_gate",
    "please restate": "clarification",
    "specify parameters": "query_prompt",
    "working": "working",
    "stand by": "working",
    "warning": "warning",
    "acknowledged": "acknowledgment",
    "affirmative": "acknowledgment",
    "negative": "negation",
    "stardate": "stardate",
    "life signs": "casualty",
    "complete": "completion",
    "located": "location",
    "detected": "warning",
}


def suggest(transcript: str) -> str:
    low = transcript.lower()
    for needle, arch in _PATTERN_TO_ARCHETYPE.items():
        if re.search(rf"\b{re.escape(needle)}\b", low):
            return arch
    for pat, _ in TEMPLATES:
        if re.search(pat, low, flags=re.IGNORECASE):
            return "status_info"
    return "unclassified"
