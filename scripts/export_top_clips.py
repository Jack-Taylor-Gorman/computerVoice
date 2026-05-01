#!/usr/bin/env python3
"""Cut top-N Majel-ranked clips to standalone WAVs for listening.

Reads storage/majel_cosine.jsonl, slices each top clip from the cached
24 kHz source WAV via ffmpeg, writes to dataset/clips_ranked/ with filenames
encoding rank, cosine, duration, and transcript snippet.

Usage:
  ./scripts/export_top_clips.py [--top 100] [--min-cosine 0.20]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trekdata.config import settings
from trekdata.db import SessionLocal
from trekdata.models import Source
from sqlalchemy import select
import asyncio


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^A-Za-z0-9 ]+", "", text or "")
    s = re.sub(r"\s+", "_", s).strip("_")
    return s[:n] or "untitled"


async def _src_map() -> dict[str, str]:
    async with SessionLocal() as db:
        srcs = (await db.execute(select(Source))).scalars().all()
        return {s.id: s.sha256 for s in srcs}


async def main(top: int, min_cos: float) -> int:
    out_dir = ROOT / "dataset" / "clips_ranked"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = [json.loads(l) for l in (ROOT / "storage" / "majel_cosine.jsonl").open()]
    rows.sort(key=lambda r: -r["cosine"])

    src_map = await _src_map()

    # Need clip_id → source_id mapping; the JSONL has source filename only.
    # Look up via source filename → sha256 → cached path.
    async with SessionLocal() as db:
        from trekdata.models import Clip, Source as SrcModel
        clip_to_src = {}
        for c in (await db.execute(select(Clip))).scalars().all():
            clip_to_src[c.id] = c.source_id

    sr = settings.target_sample_rate
    written = 0
    cum_dur = 0.0
    for rank, r in enumerate(rows, 1):
        if r["cosine"] < min_cos or written >= top:
            continue
        source_id = clip_to_src.get(r["clip_id"])
        if not source_id:
            continue
        sha = src_map.get(source_id)
        if not sha:
            continue
        cached = settings.cache_dir / f"{sha}.{sr}.wav"
        if not cached.exists():
            continue

        slug = _slug(r["transcript"])
        name = f"{rank:03d}__{r['cosine']:.3f}__{r['duration']:.1f}s__{slug}.wav"
        out = out_dir / name
        if out.exists():
            written += 1
            cum_dur += r["duration"]
            continue
        s = max(0.0, r["start"])
        d = r["end"] - s
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-ss", f"{s:.3f}", "-t", f"{d:.3f}",
             "-i", str(cached),
             "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", str(out)],
            check=True,
        )
        written += 1
        cum_dur += r["duration"]
        if written % 25 == 0:
            print(f"  …{written}/{top}", flush=True)

    print(f"\nWrote {written} clips → {out_dir}")
    print(f"Cumulative duration: {cum_dur:.1f}s ({cum_dur/60:.1f} min)")
    print(f"\nReview by playing:")
    print(f"  for f in dataset/clips_ranked/*.wav; do echo \"$f\"; paplay \"$f\"; done")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=100)
    ap.add_argument("--min-cosine", type=float, default=0.20)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.top, args.min_cosine)))
