"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    name: str
    source_kind: str = "file"
    notes: str | None = None


class SessionOut(BaseModel):
    id: str
    name: str
    source_kind: str
    created_at: datetime
    notes: str | None = None

    class Config:
        from_attributes = True


class ClipOut(BaseModel):
    id: str
    source_id: str
    parent_clip_id: str | None
    start_s: float
    end_s: float
    state: str
    lufs: float | None
    snr_db: float | None
    noise_class: str | None
    is_virtual: bool

    class Config:
        from_attributes = True


class LabelUpdate(BaseModel):
    transcript_raw: str | None = None
    archetype: str | None = None
    trigger_utterance: str | None = None
    addressee: str | None = None
    scene_context: str | None = None
    source_episode: str | None = None
    series: str | None = None
    prosody_tag: str | None = None
    quality: int | None = Field(default=None, ge=1, le=5)


class TrimRequest(BaseModel):
    start_s: float
    end_s: float


class SplitOnSilenceRequest(BaseModel):
    min_silence_ms: int = 300
    thresh_db: float = -40.0


class ExportRequest(BaseModel):
    format: str = "ljspeech"  # or "hf_datasets"
    run_id: str | None = None
    train_frac: float = 0.9
    val_frac: float = 0.05
    min_snr_db: float = 20.0
    archetypes: list[str] | None = None
