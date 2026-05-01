#!/usr/bin/env python3
"""Synchronous CLI ingest for trekdata.

Walks one or more directories of source audio, registers Source rows,
runs VAD to create Clip rows, then runs the full per-clip processing
chain (transcribe + LUFS + SNR + speaker embed + archetype suggest).

No redis/arq required — runs everything inline. Use this once to seed
the database; afterwards launch the FastAPI backend so you can review
clips through the React UI.

Usage:
  ./scripts/trekdata_ingest.py path/to/audio_dir [more dirs ...]
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trekdata.config import settings
from trekdata.db import SessionLocal, engine
from trekdata.ingest import decode, vad
from trekdata.ingest.pipeline import process_clip
from trekdata.models import Base, Clip, ClipState, Session as TrekSession, Source, SourceKind
from sqlalchemy import select

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".webm"}


async def _bootstrap_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    for p in (settings.sources_dir, settings.cache_dir, settings.datasets_dir):
        p.mkdir(parents=True, exist_ok=True)


async def _get_or_create_session(name: str) -> str:
    async with SessionLocal() as db:
        row = (await db.execute(select(TrekSession).where(TrekSession.name == name))).scalar_one_or_none()
        if row:
            return row.id
        s = TrekSession(name=name, source_kind=SourceKind.batch,
                        notes="auto-ingested via scripts/trekdata_ingest.py")
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s.id


def _probe(p: Path) -> tuple[float, int, int, str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels,codec_name:format=duration",
         "-of", "default=nw=1", str(p)],
        capture_output=True, text=True, check=True,
    ).stdout
    sr = ch = 0
    codec = "unknown"
    dur = 0.0
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if k == "sample_rate" and v.isdigit():
            sr = int(v)
        elif k == "channels" and v.isdigit():
            ch = int(v)
        elif k == "codec_name":
            codec = v
        elif k == "duration":
            try:
                dur = float(v)
            except ValueError:
                pass
    return dur, sr, ch, codec


async def _register_source(path: Path, session_id: str) -> str | None:
    digest = decode.sha256_file(path)
    async with SessionLocal() as db:
        existing = (await db.execute(select(Source).where(Source.sha256 == digest))).scalar_one_or_none()
        if existing:
            return existing.id
        dur, sr, ch, codec = _probe(path)
        s = Source(
            sha256=digest,
            path=str(path.resolve()),
            original_filename=path.name,
            duration_s=dur,
            sample_rate=sr,
            channels=ch,
            codec=codec,
            session_id=session_id,
        )
        db.add(s)
        await db.commit()
        await db.refresh(s)
        return s.id


async def _ingest(source_id: str) -> int:
    """Run VAD and create Clip rows. Returns count created."""
    async with SessionLocal() as db:
        src = await db.get(Source, source_id)
        wav = decode.decode_to_cache(Path(src.path))
        spans = vad.segment(wav)
        created = 0
        for (s, e) in spans:
            dur = e - s
            if dur < settings.min_clip_sec or dur > settings.max_clip_sec:
                continue
            db.add(Clip(source_id=src.id, start_s=s, end_s=e, state=ClipState.vad_split))
            created += 1
        await db.commit()
        return created


async def _process_pending(source_id: str, limit: int | None = None) -> int:
    async with SessionLocal() as db:
        q = select(Clip).where(Clip.source_id == source_id, Clip.state == ClipState.vad_split)
        if limit is not None:
            q = q.limit(limit)
        ids = [c.id for c in (await db.execute(q)).scalars().all()]
    for i, cid in enumerate(ids, 1):
        try:
            await process_clip(cid)
        except Exception as ex:
            print(f"   ! clip {cid[:8]}… failed: {type(ex).__name__}: {ex}", file=sys.stderr)
        if i % 20 == 0:
            print(f"   …processed {i}/{len(ids)} clips", flush=True)
    return len(ids)


async def main(dirs: list[str]) -> int:
    await _bootstrap_schema()
    session_id = await _get_or_create_session("auto-bootstrap")

    sources: list[Path] = []
    for d in dirs:
        p = Path(d).resolve()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            sources.append(p)
        elif p.is_dir():
            sources.extend(sorted(q for q in p.rglob("*") if q.suffix.lower() in AUDIO_EXTS))
    if not sources:
        print("no audio sources found", file=sys.stderr)
        return 2

    print(f"Found {len(sources)} source files. Session id: {session_id}")
    total_clips = 0
    for src in sources:
        print(f"\n[{src.name}]")
        sid = await _register_source(src, session_id)
        if not sid:
            print("  registration failed")
            continue
        n = await _ingest(sid)
        print(f"  VAD → {n} clip rows queued")
        if n == 0:
            continue
        processed = await _process_pending(sid)
        print(f"  processed {processed} clips through transcribe/lufs/snr/embed")
        total_clips += processed

    print(f"\nDone. {total_clips} clips processed.")
    print("Launch backend: ./venv/bin/uvicorn trekdata.main:app --host 127.0.0.1 --port 7862")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: trekdata_ingest.py <dir_or_file> [more ...]", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
