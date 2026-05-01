from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trekdata.db import get_session
from trekdata.models import Session, SourceKind
from trekdata.schemas import SessionCreate, SessionOut

router = APIRouter()


@router.post("", response_model=SessionOut)
async def create_session(body: SessionCreate, db: AsyncSession = Depends(get_session)) -> SessionOut:
    s = Session(name=body.name, source_kind=SourceKind(body.source_kind), notes=body.notes)
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return SessionOut.model_validate(s)


@router.get("", response_model=list[SessionOut])
async def list_sessions(db: AsyncSession = Depends(get_session)) -> list[SessionOut]:
    rows = (await db.execute(select(Session).order_by(Session.created_at.desc()))).scalars().all()
    return [SessionOut.model_validate(r) for r in rows]
