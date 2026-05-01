#!/usr/bin/env python3
"""Rank trekdata clips by ECAPA cosine similarity to a Majel reference.

Reads all `Clip` rows from the trekdata SQLite DB, computes ECAPA embeddings
on per-span audio cut from the cached source WAVs, compares against a Majel
reference embedding, and writes the ranking to:

    storage/majel_cosine.jsonl   (one JSON line per clip, sorted desc by cosine)

Side-effect: also writes the embedding bytes back to clips.speaker_embedding
so the labeling UI can read it later if needed.

Usage:
  ./scripts/rank_by_majel.py [--ref dataset/majel_ref.wav] [--top 50]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
from sqlalchemy import select

from trekdata.config import settings
from trekdata.db import SessionLocal
from trekdata.models import Clip, Label, Source


def _load_encoder():
    from speechbrain.inference.speaker import EncoderClassifier
    return EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        run_opts={"device": "cuda"},
    )


def _embed(enc, wav_path: Path, start_s: float, end_s: float) -> np.ndarray:
    import torch
    import torchaudio
    sig, sr = torchaudio.load(str(wav_path))
    if sr != 16000:
        sig = torchaudio.functional.resample(sig, sr, 16000)
        sr = 16000
    s_idx = int(start_s * sr)
    e_idx = int(end_s * sr)
    span = sig[:, s_idx:e_idx]
    if span.shape[-1] < int(0.4 * sr):
        return None
    with torch.inference_mode():
        emb = enc.encode_batch(span).squeeze().cpu().numpy().astype(np.float32)
    return emb


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


async def main(ref_path: Path, top_n: int) -> int:
    enc = _load_encoder()
    print(f"Loading Majel reference: {ref_path}")
    ref_emb = _embed(enc, ref_path, 0.0, 1e9)
    if ref_emb is None:
        print("reference too short", file=sys.stderr)
        return 2

    rows: list[dict] = []
    async with SessionLocal() as db:
        clips = (await db.execute(select(Clip))).scalars().all()
        # Map source_id -> path
        srcs = {s.id: s for s in (await db.execute(select(Source))).scalars().all()}
        labels = {l.clip_id: l for l in (await db.execute(select(Label))).scalars().all()}

        print(f"Embedding {len(clips)} clips…")
        for i, c in enumerate(clips, 1):
            src = srcs.get(c.source_id)
            if not src:
                continue
            cached = settings.cache_dir / f"{src.sha256}.{settings.target_sample_rate}.wav"
            if not cached.exists():
                continue
            try:
                emb = _embed(enc, cached, c.start_s, c.end_s)
            except Exception as ex:
                print(f"  ! clip {c.id[:8]} embed failed: {ex}", file=sys.stderr)
                continue
            if emb is None:
                continue
            cos = _cosine(emb, ref_emb)
            rows.append({
                "clip_id": c.id,
                "source": src.original_filename,
                "start": round(c.start_s, 3),
                "end": round(c.end_s, 3),
                "duration": round(c.end_s - c.start_s, 3),
                "cosine": round(cos, 4),
                "snr_db": round(c.snr_db, 1) if c.snr_db is not None else None,
                "lufs": round(c.lufs, 1) if c.lufs is not None else None,
                "transcript": (labels.get(c.id).transcript_raw if labels.get(c.id) else "") or "",
            })
            # Persist embedding bytes
            c.speaker_embedding = emb.tobytes()
            if i % 50 == 0:
                print(f"  …{i}/{len(clips)}", flush=True)
        await db.commit()

    rows.sort(key=lambda r: -r["cosine"])
    out = ROOT / "storage" / "majel_cosine.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    print(f"\nWrote {len(rows)} rankings → {out}")
    print(f"\nTop {top_n} by Majel-cosine:")
    cum_dur = 0.0
    for r in rows[:top_n]:
        cum_dur += r["duration"]
        print(f"  {r['cosine']:.3f}  {r['duration']:5.1f}s  {r['source'][:40]:<40}  {r['transcript'][:60]}")
    print(f"\nTop-{top_n} cumulative duration: {cum_dur:.1f}s ({cum_dur/60:.1f} min)")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", type=Path, default=ROOT / "dataset" / "majel_ref.wav")
    ap.add_argument("--top", type=int, default=50)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(args.ref, args.top)))
