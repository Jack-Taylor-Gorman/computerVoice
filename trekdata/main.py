"""FastAPI app entrypoint. Run: uvicorn trekdata.main:app --host 127.0.0.1 --port 7862"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from trekdata.api import clips, export, jobs, labels, sessions
from trekdata.config import settings
from trekdata.db import engine
from trekdata.models import Base

WEB_DIR = Path(__file__).resolve().parent / "web" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    for p in (settings.sources_dir, settings.cache_dir, settings.datasets_dir):
        p.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Trek Dataset Builder", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
app.include_router(clips.router, prefix="/api/clips", tags=["clips"])
app.include_router(labels.router, prefix="/api/labels", tags=["labels"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["jobs"])
app.include_router(export.router, prefix="/api/export", tags=["export"])

if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True, "version": app.version}
