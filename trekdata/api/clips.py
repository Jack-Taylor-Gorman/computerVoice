from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trekdata.db import get_session
from trekdata.models import Clip, ClipState
from trekdata.schemas import ClipOut, SplitOnSilenceRequest, TrimRequest

router = APIRouter()


@router.get("", response_model=list[ClipOut])
async def list_clips(
    state: str | None = None,
    session_id: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_session),
) -> list[ClipOut]:
    q = select(Clip).limit(limit)
    if state:
        q = q.where(Clip.state == ClipState(state))
    rows = (await db.execute(q)).scalars().all()
    return [ClipOut.model_validate(r) for r in rows]


@router.get("/{clip_id}", response_model=ClipOut)
async def get_clip(clip_id: str, db: AsyncSession = Depends(get_session)) -> ClipOut:
    c = await db.get(Clip, clip_id)
    if not c:
        raise HTTPException(404)
    return ClipOut.model_validate(c)


@router.post("/{clip_id}/trim", response_model=ClipOut)
async def trim_clip(clip_id: str, body: TrimRequest, db: AsyncSession = Depends(get_session)) -> ClipOut:
    parent = await db.get(Clip, clip_id)
    if not parent:
        raise HTTPException(404)
    child = Clip(
        source_id=parent.source_id,
        parent_clip_id=parent.id,
        start_s=parent.start_s + body.start_s,
        end_s=parent.start_s + body.end_s,
        state=parent.state,
        is_virtual=True,
    )
    db.add(child)
    await db.commit()
    await db.refresh(child)
    return ClipOut.model_validate(child)


@router.post("/{clip_id}/split_on_silence")
async def split_on_silence(clip_id: str, body: SplitOnSilenceRequest) -> dict:
    raise HTTPException(501, "enqueue silero-vad split job — implemented in trekdata.ingest.vad")
