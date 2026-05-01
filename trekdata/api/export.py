from dataclasses import asdict

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from trekdata.db import get_session
from trekdata.ingest.export import export_dataset
from trekdata.schemas import ExportRequest

router = APIRouter()


@router.post("")
async def start_export(body: ExportRequest, db: AsyncSession = Depends(get_session)) -> dict:
    summary = await export_dataset(
        db,
        run_id=body.run_id,
        train_frac=body.train_frac,
        val_frac=body.val_frac,
        min_snr_db=body.min_snr_db,
        archetype_filter=body.archetypes,
    )
    return asdict(summary)
