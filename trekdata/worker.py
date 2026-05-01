"""arq worker. Run: arq trekdata.worker.WorkerSettings"""
from __future__ import annotations

from arq.connections import RedisSettings

from trekdata.config import settings
from trekdata.ingest.pipeline import ingest_source as _ingest_source
from trekdata.ingest.pipeline import process_clip as _process_clip


async def ingest_source(ctx, source_id: str) -> None:
    await _ingest_source(source_id)
    from trekdata.db import SessionLocal
    from trekdata.models import Clip, ClipState
    from sqlalchemy import select
    async with SessionLocal() as db:
        rows = (await db.execute(select(Clip).where(Clip.source_id == source_id, Clip.state == ClipState.vad_split))).scalars().all()
        for c in rows:
            await ctx["redis"].enqueue_job("process_clip", c.id)


async def process_clip(ctx, clip_id: str) -> None:
    await _process_clip(clip_id)


class WorkerSettings:
    functions = [ingest_source, process_clip]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 2
