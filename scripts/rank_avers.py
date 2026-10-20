#!/usr/bin/env python3
"""Rank + rename the Avers audiobook clips for the curator UI.

Reads `dataset_avers/manifests/avers.jsonl` (output of `mine_audiobook.py`),
scores each clip on a single-narrator quality metric (no speaker-embedding
gate needed — it's all Roy Avers), sorts by score, and writes ranked copies
into `dataset_avers/clips_ranked/` with the filename format the curator
already speaks:

    NNN__C.CCC__D.Ds__transcript_slug.wav

  NNN          rank prefix (000001 = highest quality)
  C.CCC        composite quality score in [0.0, 1.0]
  D.Ds         duration in seconds
  slug         sanitised first-six-words of transcript

Scoring (each 0.0–1.0):
  duration_score   bell curve centred on 7s (sweet spot for F5 training)
  text_score       complete sentence, sane length, no garbage
  energy_score     RMS in usable range — neither whispered nor clipped
  position_score   first/last 5 clips of the audiobook penalised (intro / outro
                   boilerplate that Roy Avers reads but Severian doesn't say)

Composite = 0.50·duration + 0.25·text + 0.15·energy + 0.10·position.

Run AFTER `mine_audiobook.py`; idempotent — `clips_ranked/` is wiped and
rebuilt each run unless --keep-existing is passed.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = ROOT / "dataset_avers"
SRC_MANIFEST = SRC_ROOT / "manifests" / "avers.jsonl"
SRC_CLIPS = SRC_ROOT / "clips"
DST = SRC_ROOT / "clips_ranked"

# ── Tunables ─────────────────────────────────────────────────────────────
DURATION_TARGET = 7.0
DURATION_SIGMA = 3.0
DURATION_HARD_MIN = 4.0
DURATION_HARD_MAX = 16.0

TEXT_MIN_CHARS = 16
TEXT_MAX_CHARS = 220
TEXT_MIN_WORDS = 3

RMS_MIN = 0.005   # below = whispered / silence
RMS_MAX = 0.30    # above = clipping / shout

INTRO_BOILERPLATE_HEAD = 5
OUTRO_BOILERPLATE_TAIL = 5


def duration_score(dur: float) -> float:
    """Bell curve centred on DURATION_TARGET. Hard floors outside [4, 16]."""
    if dur < DURATION_HARD_MIN or dur > DURATION_HARD_MAX:
        return 0.0
    z = (dur - DURATION_TARGET) / DURATION_SIGMA
    return math.exp(-0.5 * z * z)


def text_score(text: str) -> float:
    if not text:
        return 0.0
    t = text.strip()
    if len(t) < TEXT_MIN_CHARS or len(t) > TEXT_MAX_CHARS:
        return 0.3
    words = t.split()
    if len(words) < TEXT_MIN_WORDS:
        return 0.3
    # Penalise transcripts containing obvious NLS boilerplate.
    low = t.lower()
    if any(b in low for b in (
        "american printing house for the blind",
        "narrated by",
        "cassette player", "fast forward",
        "library of congress",
        "end of the shadow of the torturer",
        "annotation",
    )):
        return 0.1
    score = 0.6
    if t.endswith((".", "?", "!")):
        score += 0.30
    # First word capitalised (suggests sentence start).
    if t[0].isupper():
        score += 0.05
    # Penalise lines that begin mid-word ("soon see by the sickly...").
    if t[0].islower():
        score -= 0.10
    # Modest bonus for length in the comfortable readable range.
    if 30 <= len(t) <= 150:
        score += 0.05
    return max(0.0, min(1.0, score))


def energy_score(wav_path: Path) -> float:
    try:
        data, sr = sf.read(str(wav_path), dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if len(data) == 0:
            return 0.0
        rms = float(np.sqrt((data * data).mean() + 1e-9))
        if rms < RMS_MIN or rms > RMS_MAX:
            return 0.2
        # 5-percentile silence detection: if a substantial fraction of
        # 50ms windows is near-silent, the clip likely has a long pause.
        win = max(1, sr // 20)
        windows = np.array_split(data, max(1, len(data) // win))
        win_rms = np.array([np.sqrt((w * w).mean() + 1e-9) for w in windows])
        silent = (win_rms < rms * 0.08).sum() / max(1, len(win_rms))
        # 0 silent windows → 1.0; 25%+ silent → 0.2
        return max(0.2, min(1.0, 1.0 - silent * 3))
    except Exception:
        return 0.0


def position_score(idx: int, total: int) -> float:
    if idx < INTRO_BOILERPLATE_HEAD:
        return 0.0
    if idx >= total - OUTRO_BOILERPLATE_TAIL:
        return 0.0
    return 1.0


def slugify(text: str, max_words: int = 6) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text or "")
    slug = "_".join(words[:max_words])
    if len(slug) > 60:
        slug = slug[:60]
    return slug or "clip"


def composite(dur_s: float, text_s: float, energy_s: float, pos_s: float) -> float:
    return 0.50 * dur_s + 0.25 * text_s + 0.15 * energy_s + 0.10 * pos_s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-existing", action="store_true",
                    help="leave existing clips_ranked/ in place and only add new ones")
    ap.add_argument("--top", type=int, default=None,
                    help="only export the top N clips (default: all)")
    ap.add_argument("--min-score", type=float, default=0.0,
                    help="drop clips scoring below this composite threshold")
    ap.add_argument("--no-energy", action="store_true",
                    help="skip per-clip RMS computation (faster, less accurate)")
    args = ap.parse_args()

    if not SRC_MANIFEST.exists():
        print(f"missing manifest: {SRC_MANIFEST}", file=sys.stderr)
        return 2
    if not SRC_CLIPS.exists():
        print(f"missing clips dir: {SRC_CLIPS}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for line in SRC_MANIFEST.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    total = len(rows)
    print(f"loaded {total} clips from {SRC_MANIFEST}")

    # Score every row.
    scored: list[tuple[float, dict, float, float, float, float]] = []
    for i, r in enumerate(rows):
        dur = float(r.get("duration", 0))
        text = r.get("text", "")
        ds = duration_score(dur)
        ts = text_score(text)
        ps = position_score(i, total)
        # Energy needs file read; expensive — gate behind flag.
        wav = SRC_CLIPS / Path(r["audio_path"]).name
        es = 1.0 if args.no_energy else energy_score(wav)
        c = composite(ds, ts, es, ps)
        scored.append((c, r, ds, ts, es, ps))
        if (i + 1) % 500 == 0:
            print(f"  scored {i+1}/{total}", flush=True)

    # Sort descending; apply threshold + top cap.
    scored.sort(key=lambda x: -x[0])
    if args.min_score > 0:
        scored = [s for s in scored if s[0] >= args.min_score]
        print(f"after --min-score {args.min_score}: {len(scored)} kept")
    if args.top:
        scored = scored[: args.top]
        print(f"after --top {args.top}: {len(scored)} kept")

    if not args.keep_existing:
        if DST.exists():
            print(f"wiping existing {DST}")
            shutil.rmtree(DST)
    DST.mkdir(parents=True, exist_ok=True)

    n_out = 0
    n_meta = 0
    score_summary = {"<0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, "0.7-0.85": 0, ">=0.85": 0}
    # Side manifest mapping new_name → full transcript + source path. The
    # curator reads this so it can show the entire WhisperX transcript
    # (not just the 6-word slug encoded in the filename).
    metadata_path = DST / "_metadata.jsonl"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with metadata_path.open("w") as meta_f:
        for rank, (c, r, ds, ts, es, ps) in enumerate(scored, start=1):
            wav = SRC_CLIPS / Path(r["audio_path"]).name
            if not wav.exists():
                continue
            slug = slugify(r.get("text", ""))
            new_name = f"{rank:06d}__{c:.3f}__{r['duration']:.1f}s__{slug}.wav"
            new_path = DST / new_name
            if not (args.keep_existing and new_path.exists()):
                shutil.copy2(wav, new_path)
                n_out += 1
            meta_f.write(json.dumps({
                "filename": new_name,
                "src": r["audio_path"],
                "text": r.get("text", ""),
                "duration": r.get("duration"),
                "score": round(c, 4),
                "rank": rank,
                "scores": {"duration": round(ds, 3), "text": round(ts, 3),
                           "energy": round(es, 3), "position": round(ps, 3)},
            }) + "\n")
            n_meta += 1
            bucket = ("<0.3" if c < 0.3 else
                      "0.3-0.5" if c < 0.5 else
                      "0.5-0.7" if c < 0.7 else
                      "0.7-0.85" if c < 0.85 else
                      ">=0.85")
            score_summary[bucket] += 1
            if rank % 500 == 0:
                print(f"  exported {rank}/{len(scored)}", flush=True)

    print(f"\nwrote {n_out} new clips ({n_meta} total in metadata) to {DST}")
    print(f"metadata: {metadata_path}")
    print(f"score distribution:")
    for k, v in score_summary.items():
        print(f"  {k:>9s}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
