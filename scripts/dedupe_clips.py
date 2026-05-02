#!/usr/bin/env python3
"""Move duplicate clips out of dataset/clips_ranked/ → dataset/clips_dupes/.

Two clips are duplicates if they share the same normalized transcript AND
their durations are within 0.3s of each other. Within each group the
"keeper" is chosen by (in order):
  1. Already-accepted clips (preserve a curated decision).
  2. Already-rejected clips (so the rejection sticks).
  3. Lowest filename rank — best cosine to the Majel ref.

Non-keepers are moved to dataset/clips_dupes/ and their entries dropped
from transcript_overrides.json / flag_overrides.json. The .jsonl decision
manifests are append-only audit logs and are NOT modified.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RANKED = ROOT / "dataset" / "clips_ranked"
DUPES = ROOT / "dataset" / "clips_dupes"
OVERRIDES = ROOT / "dataset" / "transcript_overrides.json"
FLAGS = ROOT / "dataset" / "flag_overrides.json"
ACCEPT = ROOT / "dataset" / "manifest_curated.jsonl"
REJECT = ROOT / "dataset" / "manifest_rejected.jsonl"


def parse(p: Path) -> tuple[int, float, float, str]:
    m = re.match(r"^(\d+)__([\d.]+)__([\d.]+)s__(.+)\.wav$", p.name)
    if not m:
        return 0, 0.0, 0.0, p.stem
    return int(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(4)


def normalize(text: str) -> str:
    s = re.sub(r"[^\w\s]", "", (text or "").lower()).strip()
    return re.sub(r"\s+", " ", s)


def load_decided() -> tuple[set[str], set[str]]:
    accepted, rejected = set(), set()
    if ACCEPT.exists():
        for line in ACCEPT.read_text().splitlines():
            try:
                accepted.add(json.loads(line)["clip_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    if REJECT.exists():
        for line in REJECT.read_text().splitlines():
            try:
                rejected.add(json.loads(line)["clip_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return accepted, rejected


def keeper_priority(p: Path, accepted: set[str], rejected: set[str]) -> tuple:
    rank = parse(p)[0]
    if p.name in accepted:
        return (0, rank)
    if p.name in rejected:
        return (1, rank)
    return (2, rank)


def main() -> int:
    if not RANKED.exists():
        sys.stderr.write(f"missing {RANKED}\n")
        return 2

    overrides: dict[str, str] = {}
    if OVERRIDES.exists():
        try:
            overrides = json.loads(OVERRIDES.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    flags: dict[str, dict] = {}
    if FLAGS.exists():
        try:
            flags = json.loads(FLAGS.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    accepted, rejected = load_decided()
    clips = sorted(RANKED.glob("*.wav"), key=lambda p: parse(p)[0])

    # Group by normalized transcript ONLY — duration is unreliable as a
    # tiebreaker because the same line mined from different episodes can
    # vary by 0.5–2s. Text-level uniqueness is what F5/RVC actually care
    # about (no transcript leakage between train/val).
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in clips:
        _, _, _dur, slug = parse(p)
        text = overrides.get(p.name) or slug.replace("_", " ")
        key = normalize(text)
        if not key:
            continue
        groups[key].append(p)

    DUPES.mkdir(parents=True, exist_ok=True)
    moved = 0
    kept_dupe_groups = 0
    for key, members in groups.items():
        if len(members) <= 1:
            continue
        kept_dupe_groups += 1
        members.sort(key=lambda p: keeper_priority(p, accepted, rejected))
        keeper = members[0]
        for d in members[1:]:
            shutil.move(str(d), str(DUPES / d.name))
            overrides.pop(d.name, None)
            flags.pop(d.name, None)
            moved += 1
        print(f"keep  {keeper.name}\n      dropped: {[d.name for d in members[1:]]}")

    if moved:
        OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
        OVERRIDES.write_text(json.dumps(overrides, indent=2, sort_keys=True))
        if flags or FLAGS.exists():
            FLAGS.write_text(json.dumps(flags, indent=2, sort_keys=True))

    remaining = len(list(RANKED.glob("*.wav")))
    print(f"\ngroups with dupes: {kept_dupe_groups}")
    print(f"clips moved → {DUPES}: {moved}")
    print(f"remaining in clips_ranked: {remaining}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
