from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trekdata.db import get_session
from trekdata.models import JobStatus, ProcessingJob

router = APIRouter()


@router.get("")
async def list_jobs(status: str | None = None, db: AsyncSession = Depends(get_session)) -> list[dict]:
    q = select(ProcessingJob).order_by(ProcessingJob.started_at.desc()).limit(200)
    if status:
        q = q.where(ProcessingJob.status == JobStatus(status))
    rows = (await db.execute(q)).scalars().all()
    return [
        {
            "id": j.id,
            "clip_id": j.clip_id,
            "step": j.step,
            "status": j.status.value,
            "attempts": j.attempts,
            "error": j.error,
        }
        for j in rows
    ]
