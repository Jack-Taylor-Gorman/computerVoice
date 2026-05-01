"""Phonemizer (espeak-ng) IPA transcription + corpus coverage tracker."""
from __future__ import annotations

import json
from pathlib import Path


def to_ipa(text: str) -> str:
    from phonemizer import phonemize
    return phonemize(text, language="en-us", backend="espeak", strip=True, preserve_punctuation=False)


def coverage_update(ipa: str, current: set[str]) -> set[str]:
    return current | {ch for ch in ipa if not ch.isspace()}


def save_coverage(path: Path, coverage: set[str]) -> None:
    path.write_text(json.dumps(sorted(coverage), ensure_ascii=False, indent=2))


def load_coverage(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text()))
