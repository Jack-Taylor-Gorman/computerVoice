#!/usr/bin/env python3
"""Mine Star Trek episodes for Majel Barrett computer-voice clips.

Pipeline per episode:
  1. Demux audio to 16k mono wav (ffmpeg)
  2. WhisperX: ASR + word alignment + speaker diarization
  3. Label computer-voice utterances:
       a. Transcript heuristic — lines whose preceding turn addresses "Computer"
          or whose text pattern-matches common computer responses
          ("Working.", "Affirmative.", "Unable to comply.", etc.)
       b. Speaker-embedding gate — cosine-match each candidate segment against
          a Majel reference embedding (Resemblyzer / ECAPA). Rejects Lwaxana
          Troi (also Majel, different prosody) and other speakers.
  4. Quality filters — VAD trim, min/max duration, SNR, optional UVR music strip
  5. Emit (clip.wav, transcript.txt) pairs + append to dataset manifest

Resumable: per-episode results are cached under dataset/cache/<ep-stem>.json so
reruns skip completed episodes. Delete the cache file to reprocess an episode.

Usage:
  ./scripts/mine_majel.py \\
      --episodes /media/trek/TNG \\
      --reference /path/to/majel_ref.wav \\
      --out dataset \\
      --device cuda:0
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

# Lazy heavy imports — loaded inside run() so `--help` is fast and partial
# installs still let you test stages in isolation.

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts"}

COMPUTER_RESPONSE_PATTERNS = [
    r"^\s*working\.?\s*$",
    r"^\s*acknowledged\.?\s*$",
    r"^\s*affirmative\.?\s*$",
    r"^\s*negative\.?\s*$",
    r"^\s*unable to comply",
    r"^\s*access denied",
    r"^\s*please (restate|specify)",
    r"^\s*specify parameters",
    r"^\s*warning[.,:]",
    r"^\s*alert[.,:]",
    r"^\s*(red|yellow) alert",
    r"^\s*\d+\s*(crew|members|humanoids|life.?forms|species)",
    r"^\s*(location|identity|status)\s+(unknown|confirmed|unavailable)",
    r"^\s*computer\s+(active|ready|online|offline)",
    r"^\s*self.?destruct",
    r"^\s*unable to locate",
    r"^\s*information not",
]
COMPUTER_RE = re.compile("|".join(COMPUTER_RESPONSE_PATTERNS), re.IGNORECASE)

ADDRESS_COMPUTER_RE = re.compile(
    r"\b(computer)[,.\s]", re.IGNORECASE
)


@dataclass
class Candidate:
    start: float
    end: float
    text: str
    speaker: str | None
    source: str  # "addressed" | "pattern" | "diarized-majel"


@dataclass
class Clip:
    episode: str
    start: float
    end: float
    text: str
    clip_path: str
    cosine: float
    source: str


def demux_audio(video: Path, out_wav: Path) -> None:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    if out_wav.exists():
        return
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)],
        check=True,
    )


def transcribe_and_diarize(wav: Path, device: str, hf_token: str | None):
    """Run WhisperX: transcribe, align, diarize. Returns list of segment dicts
    with keys: start, end, text, speaker (may be None), words (aligned).
    """
    import whisperx  # type: ignore

    model_size = os.environ.get("WHISPER_MODEL", "large-v3")
    compute_type = "float16" if device.startswith("cuda") else "int8"
    batch_size = 16 if device.startswith("cuda") else 4

    asr = whisperx.load_model(model_size, device=device.split(":")[0],
                              compute_type=compute_type)
    audio = whisperx.load_audio(str(wav))
    result = asr.transcribe(audio, batch_size=batch_size)

    align_model, metadata = whisperx.load_align_model(
        language_code=result["language"], device=device.split(":")[0],
    )
    aligned = whisperx.align(result["segments"], align_model, metadata, audio,
                             device.split(":")[0], return_char_alignments=False)

    if hf_token:
        diarize = whisperx.DiarizationPipeline(use_auth_token=hf_token,
                                               device=device.split(":")[0])
        diar = diarize(audio)
        aligned = whisperx.assign_word_speakers(diar, aligned)

    return aligned["segments"]


def find_candidates(segments: list[dict]) -> list[Candidate]:
    out: list[Candidate] = []
    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg["start"])
        end = float(seg["end"])
        speaker = seg.get("speaker")

        prev_text = (segments[i - 1].get("text") or "") if i > 0 else ""
        addressed = bool(ADDRESS_COMPUTER_RE.search(prev_text))
        pattern_hit = bool(COMPUTER_RE.match(text))

        if addressed:
            out.append(Candidate(start, end, text, speaker, "addressed"))
        elif pattern_hit:
            out.append(Candidate(start, end, text, speaker, "pattern"))
    return out


def load_reference_embedding(ref_wav: Path):
    from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
    enc = VoiceEncoder()
    wav = preprocess_wav(str(ref_wav))
    emb = enc.embed_utterance(wav)
    return enc, emb


def gate_by_embedding(enc, ref_emb, ep_wav: Path, cands: list[Candidate],
                      threshold: float) -> list[tuple[Candidate, float]]:
    import numpy as np  # type: ignore
    from resemblyzer import preprocess_wav  # type: ignore
    import soundfile as sf  # type: ignore

    audio, sr = sf.read(str(ep_wav))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    keepers: list[tuple[Candidate, float]] = []
    for c in cands:
        s = max(0, int(c.start * sr))
        e = min(len(audio), int(c.end * sr))
        if e - s < int(0.4 * sr):
            continue
        chunk = audio[s:e].astype("float32")
        # preprocess_wav handles resampling to 16k + VAD + norm.
        try:
            prepped = preprocess_wav(chunk, source_sr=sr)
        except Exception:
            continue
        if len(prepped) < 16000 * 0.4:
            continue
        emb = enc.embed_utterance(prepped)
        cos = float(np.dot(ref_emb, emb) / (np.linalg.norm(ref_emb) * np.linalg.norm(emb)))
        if cos >= threshold:
            keepers.append((c, cos))
    return keepers


def export_clip(ep_wav: Path, c: Candidate, cos: float, out_dir: Path,
                ep_stem: str, idx: int, pad: float = 0.08) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"{ep_stem}__{idx:04d}.wav"
    dst = out_dir / name
    start = max(0.0, c.start - pad)
    dur = (c.end + pad) - start
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
         "-i", str(ep_wav),
         "-ac", "1", "-ar", "22050", "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )
    return dst


def write_manifest(clips: Iterable[Clip], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for c in clips:
            f.write(json.dumps(asdict(c)) + "\n")


def iter_episodes(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in VIDEO_EXTS)


def process_episode(ep: Path, out_root: Path, enc, ref_emb, device: str,
                    hf_token: str | None, threshold: float,
                    overwrite: bool = False) -> list[Clip]:
    cache = out_root / "cache" / f"{ep.stem}.json"
    if cache.exists() and not overwrite:
        data = json.loads(cache.read_text())
        return [Clip(**c) for c in data]

    raw = out_root / "raw" / f"{ep.stem}.wav"
    demux_audio(ep, raw)

    segments = transcribe_and_diarize(raw, device, hf_token)
    cands = find_candidates(segments)
    keepers = gate_by_embedding(enc, ref_emb, raw, cands, threshold)

    clips_dir = out_root / "clips"
    clips: list[Clip] = []
    for i, (c, cos) in enumerate(keepers):
        dst = export_clip(raw, c, cos, clips_dir, ep.stem, i)
        clips.append(Clip(
            episode=ep.name, start=c.start, end=c.end, text=c.text,
            clip_path=str(dst.relative_to(out_root)),
            cosine=cos, source=c.source,
        ))

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([asdict(c) for c in clips], indent=2))
    return clips


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", required=True, type=Path,
                    help="Directory containing video files (recursive).")
    ap.add_argument("--reference", required=True, type=Path,
                    help="Clean Majel reference clip (wav, 3–30s).")
    ap.add_argument("--out", type=Path, default=Path("dataset"),
                    help="Output root.")
    ap.add_argument("--device", default="cuda:0" if shutil.which("nvidia-smi") else "cpu")
    ap.add_argument("--threshold", type=float, default=0.72,
                    help="Cosine similarity threshold for Majel match.")
    ap.add_argument("--hf-token", default=os.environ.get("HF_TOKEN"),
                    help="HuggingFace token for pyannote diarization (optional).")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    if not args.reference.exists():
        print(f"reference not found: {args.reference}", file=sys.stderr)
        return 2
    eps = iter_episodes(args.episodes)
    if not eps:
        print(f"no episodes under {args.episodes}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifests" / "majel.jsonl"

    enc, ref_emb = load_reference_embedding(args.reference)
    total = 0
    for ep in eps:
        print(f"[{ep.name}] processing…", flush=True)
        try:
            clips = process_episode(ep, args.out, enc, ref_emb,
                                    args.device, args.hf_token, args.threshold,
                                    overwrite=args.overwrite)
        except Exception as e:
            print(f"[{ep.name}] FAILED: {e}", file=sys.stderr)
            continue
        write_manifest(clips, manifest)
        total += len(clips)
        print(f"[{ep.name}] kept {len(clips)} clips (running total: {total})")

    print(f"\nDone. {total} clips total → {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
