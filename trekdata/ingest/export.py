"""Export approved clips to an F5-TTS / LJSpeech-compatible dataset.

Layout:
  datasets/<run_id>/
    wavs/<clip_id>.wav           24 kHz mono 16-bit, normalized to -23 LUFS
    metadata.csv                 LJSpeech: clip_id|transcript|normalized
    metadata.jsonl               full record per line
    splits/{train,val,test}.txt  clip_ids
    phoneme_coverage.json
    dataset_card.md
    checksums.sha256
    export_manifest.json
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from trekdata.config import settings
from trekdata.ingest import loudness, phoneme
from trekdata.models import Clip, ClipState, Label, Source

_NORMALIZE_RE = re.compile(r"[^a-z0-9\s]")


def normalize_transcript(text: str) -> str:
    t = _NORMALIZE_RE.sub("", text.lower())
    return re.sub(r"\s+", " ", t).strip()


def _split_bucket(clip_id: str, train_frac: float, val_frac: float) -> str:
    h = int(hashlib.sha1(clip_id.encode()).hexdigest(), 16) / (1 << 160)
    if h < train_frac:
        return "train"
    if h < train_frac + val_frac:
        return "val"
    return "test"


def _materialize_trim(src_audio: Path, dst_wav: Path, start_s: float, end_s: float, target_sr: int) -> None:
    dst_wav.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{start_s:.3f}", "-to", f"{end_s:.3f}",
         "-i", str(src_audio),
         "-ar", str(target_sr), "-ac", "1", "-acodec", "pcm_s16le", str(dst_wav)],
        check=True,
    )


@dataclass
class ExportSummary:
    run_id: str
    path: str
    clip_count: int
    hours: float
    archetypes: dict
    phoneme_coverage: int


async def export_dataset(
    db: AsyncSession,
    run_id: str | None = None,
    train_frac: float = 0.9,
    val_frac: float = 0.05,
    min_snr_db: float = 20.0,
    archetype_filter: list[str] | None = None,
) -> ExportSummary:
    rid = run_id or datetime.utcnow().strftime("trek_%Y%m%d_%H%M%S")
    out = settings.datasets_dir / rid
    (out / "wavs").mkdir(parents=True, exist_ok=True)
    (out / "splits").mkdir(parents=True, exist_ok=True)

    q = select(Clip, Label, Source).join(Label, Label.clip_id == Clip.id).join(Source, Source.id == Clip.source_id).where(Clip.state == ClipState.approved)
    if min_snr_db is not None:
        q = q.where((Clip.snr_db == None) | (Clip.snr_db >= min_snr_db))  # noqa: E711

    rows = (await db.execute(q)).all()
    if not rows:
        raise RuntimeError("no approved clips match filter")

    manifest_lines: list[str] = []
    csv_lines: list[str] = []
    splits: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    archetype_hours: dict[str, float] = {}
    total_hours = 0.0
    coverage: set[str] = set()
    checksums: list[str] = []

    for clip, label, source in rows:
        if archetype_filter and label.archetype not in archetype_filter:
            continue
        transcript = (label.transcript_raw or "").strip()
        if not transcript:
            continue

        dst_raw = out / "wavs" / f"{clip.id}.raw.wav"
        dst = out / "wavs" / f"{clip.id}.wav"
        _materialize_trim(Path(source.path), dst_raw, clip.start_s, clip.end_s, settings.target_sample_rate)
        try:
            lufs_pre = loudness.normalize_to(dst_raw, dst, target_lufs=settings.target_lufs)
        except Exception:
            dst_raw.rename(dst)
            lufs_pre = clip.lufs
        if dst_raw.exists():
            dst_raw.unlink()

        norm_text = normalize_transcript(transcript)
        ipa = label.phonemes or ""
        if not ipa:
            try:
                ipa = phoneme.to_ipa(transcript)
            except Exception:
                ipa = ""
        coverage = phoneme.coverage_update(ipa, coverage)

        duration = float(clip.end_s - clip.start_s)
        total_hours += duration / 3600.0
        key = label.archetype or "unclassified"
        archetype_hours[key] = archetype_hours.get(key, 0.0) + duration / 3600.0

        bucket = _split_bucket(clip.id, train_frac, val_frac)
        splits[bucket].append(clip.id)

        emb_b64 = base64.b64encode(clip.speaker_embedding).decode() if clip.speaker_embedding else None
        row = {
            "clip_id": clip.id,
            "audio_path": f"wavs/{clip.id}.wav",
            "transcript": transcript,
            "transcript_normalized": norm_text,
            "duration_sec": round(duration, 3),
            "speaker_id": label.speaker_label or "majel_barrett",
            "series": label.series,
            "source_episode": label.source_episode,
            "source_sha256": source.sha256,
            "snr_db": clip.snr_db,
            "noise_class": clip.noise_class.value if clip.noise_class else None,
            "lufs_pre": lufs_pre,
            "lufs_target": settings.target_lufs,
            "archetype": label.archetype,
            "prosody_tag": label.prosody_tag,
            "trigger_utterance": label.trigger_utterance,
            "addressee": label.addressee,
            "scene_context": label.scene_context,
            "phonemes": ipa,
            "word_alignments": label.word_alignments,
            "speaker_embedding_b64": emb_b64,
            "correction_weight": label.correction_weight,
            "reviewed_by_human": label.reviewed_by_human,
        }
        manifest_lines.append(json.dumps(row, ensure_ascii=False))
        csv_lines.append(f"{clip.id}|{transcript}|{norm_text}")
        checksums.append(f"{hashlib.sha256(dst.read_bytes()).hexdigest()}  wavs/{clip.id}.wav")

        clip.state = ClipState.exported

    (out / "metadata.jsonl").write_text("\n".join(manifest_lines) + "\n")
    (out / "metadata.csv").write_text("\n".join(csv_lines) + "\n")
    for name, ids in splits.items():
        (out / "splits" / f"{name}.txt").write_text("\n".join(ids) + ("\n" if ids else ""))
    (out / "phoneme_coverage.json").write_text(json.dumps(sorted(coverage), ensure_ascii=False, indent=2))
    (out / "checksums.sha256").write_text("\n".join(checksums) + "\n")
    card = (
        f"# Trek Computer Voice Dataset — {rid}\n\n"
        f"- Clips: {len(manifest_lines)}\n"
        f"- Hours: {total_hours:.3f}\n"
        f"- Phoneme coverage: {len(coverage)} IPA symbols\n"
        f"- Sample rate: {settings.target_sample_rate} Hz mono 16-bit\n"
        f"- Loudness target: {settings.target_lufs} LUFS\n"
        f"- Min SNR (dB): {min_snr_db}\n"
        f"- Archetypes (hours):\n"
        + "".join(f"  - {k}: {v:.3f}\n" for k, v in sorted(archetype_hours.items(), key=lambda kv: -kv[1]))
    )
    (out / "dataset_card.md").write_text(card)
    (out / "export_manifest.json").write_text(json.dumps({
        "run_id": rid,
        "created_at": datetime.utcnow().isoformat(),
        "schema_version": 1,
        "clip_count": len(manifest_lines),
        "hours": total_hours,
        "min_snr_db": min_snr_db,
        "train_frac": train_frac,
        "val_frac": val_frac,
    }, indent=2))

    await db.commit()
    return ExportSummary(
        run_id=rid, path=str(out), clip_count=len(manifest_lines),
        hours=total_hours, archetypes=archetype_hours, phoneme_coverage=len(coverage),
    )
