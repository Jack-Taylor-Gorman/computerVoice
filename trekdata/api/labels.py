from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trekdata.archetypes import BY_KEY
from trekdata.db import get_session
from trekdata.models import Clip, ClipState, Label
from trekdata.schemas import LabelUpdate

router = APIRouter()


@router.get("/archetypes")
async def archetypes() -> list[dict]:
    from trekdata.archetypes import ARCHETYPES

    return [
        {"key": a.key, "shortcut": a.shortcut, "label": a.label, "template": a.template}
        for a in ARCHETYPES
    ]


@router.put("/{clip_id}")
async def put_label(clip_id: str, body: LabelUpdate, db: AsyncSession = Depends(get_session)) -> dict:
    clip = await db.get(Clip, clip_id)
    if not clip:
        raise HTTPException(404)
    if body.archetype and body.archetype not in BY_KEY:
        raise HTTPException(422, f"unknown archetype {body.archetype}")
    row = (await db.execute(select(Label).where(Label.clip_id == clip_id))).scalar_one_or_none()
    if not row:
        row = Label(clip_id=clip_id)
        db.add(row)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(row, k, v)
    if body.transcript_raw is not None:
        row.reviewed_by_human = True
        row.correction_weight = 2.0
    await db.commit()
    return {"ok": True}


@router.post("/{clip_id}/approve")
async def approve(clip_id: str, db: AsyncSession = Depends(get_session)) -> dict:
    clip = await db.get(Clip, clip_id)
    if not clip:
        raise HTTPException(404)
    clip.state = ClipState.approved
    await db.commit()
    return {"ok": True}


@router.post("/{clip_id}/reject")
async def reject(clip_id: str, db: AsyncSession = Depends(get_session)) -> dict:
    clip = await db.get(Clip, clip_id)
    if not clip:
        raise HTTPException(404)
    clip.state = ClipState.rejected
    await db.commit()
    return {"ok": True}
