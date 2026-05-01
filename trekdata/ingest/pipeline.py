"""End-to-end ingest orchestration for one source file (synchronous version).

Used by the arq worker or the CLI. Advances clips through the state machine.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trekdata.config import settings
from trekdata.db import SessionLocal
from trekdata.ingest import (
    align,
    archetype_tag,
    decode,
    embed,
    loudness,
    phoneme,
    snr,
    transcribe,
    vad,
)
from trekdata.models import Clip, ClipState, Label, Source

log = logging.getLogger(__name__)


async def ingest_source(source_id: str) -> None:
    async with SessionLocal() as db:
        src = await db.get(Source, source_id)
        if not src:
            return
        wav = decode.decode_to_cache(Path(src.path))
        spans = vad.segment(wav)
        for (s, e) in spans:
            dur = e - s
            if dur < settings.min_clip_sec or dur > settings.max_clip_sec:
                continue
            c = Clip(source_id=src.id, start_s=s, end_s=e, state=ClipState.vad_split)
            db.add(c)
        await db.commit()


async def process_clip(clip_id: str) -> None:
    async with SessionLocal() as db:
        c = await db.get(Clip, clip_id)
        if not c:
            return
        src = await db.get(Source, c.source_id)
        wav = decode.decode_to_cache(Path(src.path))

        tr = transcribe.transcribe(wav, c.start_s, c.end_s)
        c.state = ClipState.transcribed
        label = (await db.execute(select(Label).where(Label.clip_id == c.id))).scalar_one_or_none() or Label(clip_id=c.id)
        label.transcript_raw = tr["text"]
        label.word_alignments = tr["words"]
        db.add(label)

        try:
            c.lufs = loudness.measure_lufs(wav)
        except Exception as ex:
            log.warning("lufs failed: %s", ex)
        c.state = ClipState.normalized

        try:
            c.snr_db = snr.wada_snr(wav)
        except Exception as ex:
            log.warning("snr failed: %s", ex)
        c.state = ClipState.snr_scored

        try:
            label.phonemes = phoneme.to_ipa(tr["text"])
        except Exception as ex:
            log.warning("phoneme failed: %s", ex)

        label.archetype = archetype_tag.suggest(tr["text"])
        c.state = ClipState.labeled
        c.updated_at = datetime.utcnow()
        await db.commit()
