"""SQLAlchemy ORM models — see spec in /home/jackgorman/.../computerVoice ROADMAP."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class SourceKind(str, enum.Enum):
    mic = "mic"
    file = "file"
    batch = "batch"
    generated = "generated"


class ClipState(str, enum.Enum):
    ingested = "ingested"
    vad_split = "vad_split"
    transcribed = "transcribed"
    diarized = "diarized"
    aligned = "aligned"
    normalized = "normalized"
    snr_scored = "snr_scored"
    embedded = "embedded"
    labeled = "labeled"
    approved = "approved"
    rejected = "rejected"
    exported = "exported"


class NoiseClass(str, enum.Enum):
    clean = "clean"
    music_bg = "music_bg"
    ambient = "ambient"
    speech_bleed = "speech_bleed"
    processing_artifact = "processing_artifact"


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    success = "success"
    failed = "failed"
    retry = "retry"


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    source_kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    sources: Mapped[list[Source]] = relationship(back_populates="session")


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    path: Mapped[str] = mapped_column(String)
    original_filename: Mapped[str] = mapped_column(String(500))
    duration_s: Mapped[float] = mapped_column(Float)
    sample_rate: Mapped[int] = mapped_column(Integer)
    channels: Mapped[int] = mapped_column(Integer)
    codec: Mapped[str] = mapped_column(String(50))
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped[Session] = relationship(back_populates="sources")
    clips: Mapped[list[Clip]] = relationship(back_populates="source")


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    parent_clip_id: Mapped[str | None] = mapped_column(ForeignKey("clips.id"), nullable=True, index=True)
    start_s: Mapped[float] = mapped_column(Float)
    end_s: Mapped[float] = mapped_column(Float)
    state: Mapped[ClipState] = mapped_column(Enum(ClipState), default=ClipState.ingested, index=True)
    lufs: Mapped[float | None] = mapped_column(Float, nullable=True)
    snr_db: Mapped[float | None] = mapped_column(Float, nullable=True)
    noise_class: Mapped[NoiseClass | None] = mapped_column(Enum(NoiseClass), nullable=True)
    speaker_embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    is_virtual: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    source: Mapped[Source] = relationship(back_populates="clips")
    label: Mapped[Label | None] = relationship(back_populates="clip", uselist=False)


class Label(Base):
    __tablename__ = "labels"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id"), unique=True)
    transcript_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    phonemes: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_alignments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    archetype: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    syntactic_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_utterance: Mapped[str | None] = mapped_column(Text, nullable=True)
    addressee: Mapped[str | None] = mapped_column(String(100), nullable=True)
    scene_context: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_episode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    series: Mapped[str | None] = mapped_column(String(10), nullable=True)
    speaker_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prosody_tag: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reviewed_by_human: Mapped[bool] = mapped_column(Boolean, default=False)
    correction_weight: Mapped[float] = mapped_column(Float, default=1.0)
    quality: Mapped[int | None] = mapped_column(Integer, nullable=True)

    clip: Mapped[Clip] = relationship(back_populates="label")


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    clip_id: Mapped[str] = mapped_column(ForeignKey("clips.id"), index=True)
    step: Mapped[str] = mapped_column(String(50))
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
