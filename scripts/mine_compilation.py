#!/usr/bin/env python3
"""Mine Majel clips from compilation audio (YouTube rips, fan compilations).

Different from mine_majel.py (which mines whole episodes):
- No transcript-speaker-label heuristic (compilations have no turn structure).
- Pure audio pipeline: silence-split → transcribe → embedding-gate.
- Accepts audio files directly (mp3/wav/m4a/etc.) or a directory of them.

Pipeline per source file:
  1. Normalize → 16k mono wav (ffmpeg).
  2. Silence-split: detect gaps ≥ --gap seconds at ≤ --silence-db, cut.
  3. Per-chunk filters: duration bounds, peak level gate.
  4. Transcribe each chunk with Whisper (word-level not needed here).
  5. Embedding gate: Resemblyzer cosine vs Majel reference. Rejects characters
     whose lines appear between computer responses in the compilation.
  6. Emit each keeper as dataset/clips/<stem>__<idx>.wav + line in manifest.

Usage:
  ./scripts/mine_compilation.py \\
      --input path/to/compilation.mp3 \\
      --reference dataset/majel_ref.wav \\
      --out dataset

For YouTube sources, first download with yt-dlp:
  yt-dlp -x --audio-format mp3 -o "dataset/yt/%(title)s.%(ext)s" <url>
Then point --input at that directory.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac", ".webm"}


@dataclass
class Clip:
    source: str
    start: float
    end: float
    duration: float
    text: str
    cosine: float
    clip_path: str


def normalize(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )


def silence_split(wav: Path, gap: float, silence_db: float) -> list[tuple[float, float]]:
    """Return list of (start, end) seconds for non-silent segments."""
    r = subprocess.run(
        ["ffmpeg", "-i", str(wav), "-af",
         f"silencedetect=noise={silence_db}dB:d={gap}",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    # ffprobe for total duration
    dur_proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(wav)],
        capture_output=True, text=True,
    )
    total = float(dur_proc.stdout.strip())

    starts: list[float] = []
    ends: list[float] = []
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start:\s*([\d.]+)", line)
        if m:
            starts.append(float(m.group(1)))
            continue
        m = re.search(r"silence_end:\s*([\d.]+)", line)
        if m:
            ends.append(float(m.group(1)))

    # Build non-silent spans.
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in zip(starts, ends + [total] * (len(starts) - len(ends))):
        if s > cursor:
            spans.append((cursor, s))
        cursor = e
    if cursor < total:
        spans.append((cursor, total))
    return spans


def cut_chunk(wav: Path, start: float, end: float, out_wav: Path, pad: float = 0.08):
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    s = max(0.0, start - pad)
    d = (end + pad) - s
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{s:.3f}", "-t", f"{d:.3f}",
         "-i", str(wav), "-ac", "1", "-ar", "22050",
         "-c:a", "pcm_s16le", str(out_wav)],
        check=True,
    )


def load_reference(ref: Path):
    from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
    enc = VoiceEncoder()
    return enc, enc.embed_utterance(preprocess_wav(str(ref)))


def embed_chunk(enc, path: Path) -> float | None:
    from resemblyzer import preprocess_wav  # type: ignore
    try:
        wav = preprocess_wav(str(path))
    except Exception:
        return None
    if len(wav) < 16000 * 0.4:
        return None
    return enc.embed_utterance(wav)


def cosine(a, b) -> float:
    import numpy as np  # type: ignore
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def transcribe(wav: Path, whisper_model) -> str:
    result = whisper_model.transcribe(str(wav), language="en", fp16=False, verbose=False)
    return (result.get("text") or "").strip()


def iter_inputs(path: Path) -> list[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in AUDIO_EXTS else []
    return sorted(p for p in path.rglob("*") if p.suffix.lower() in AUDIO_EXTS)


def process(src: Path, out: Path, enc, ref_emb, whisper_model,
            threshold: float, gap: float, silence_db: float,
            min_dur: float, max_dur: float) -> list[Clip]:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", src.stem)[:80]
    norm = out / "raw" / f"{stem}.wav"
    normalize(src, norm)

    spans = silence_split(norm, gap=gap, silence_db=silence_db)
    kept: list[Clip] = []
    cache = out / "cache" / f"{stem}.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        return [Clip(**c) for c in data]

    idx = 0
    for s, e in spans:
        dur = e - s
        if dur < min_dur or dur > max_dur:
            continue
        clip_path = out / "clips" / f"{stem}__{idx:04d}.wav"
        cut_chunk(norm, s, e, clip_path)

        emb = embed_chunk(enc, clip_path)
        if emb is None:
            clip_path.unlink(missing_ok=True)
            continue
        cos = cosine(ref_emb, emb)
        if cos < threshold:
            clip_path.unlink(missing_ok=True)
            continue

        text = transcribe(clip_path, whisper_model)
        kept.append(Clip(
            source=src.name, start=s, end=e, duration=dur,
            text=text, cosine=cos,
            clip_path=str(clip_path.relative_to(out)),
        ))
        idx += 1

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps([asdict(c) for c in kept], indent=2))
    return kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path,
                    help="Audio file or directory of audio files.")
    ap.add_argument("--reference", required=True, type=Path)
    ap.add_argument("--out", type=Path, default=Path("dataset"))
    ap.add_argument("--threshold", type=float, default=0.72,
                    help="Cosine similarity gate for Majel match.")
    ap.add_argument("--gap", type=float, default=0.45,
                    help="Minimum silence gap between utterances (seconds).")
    ap.add_argument("--silence-db", type=float, default=-35.0,
                    help="Silence detection threshold in dB.")
    ap.add_argument("--min-dur", type=float, default=0.5)
    ap.add_argument("--max-dur", type=float, default=15.0)
    ap.add_argument("--whisper-model", default="small",
                    help="Whisper model size (tiny/base/small/medium/large-v3).")
    args = ap.parse_args()

    if not args.reference.exists():
        print(f"reference not found: {args.reference}", file=sys.stderr)
        return 2
    sources = iter_inputs(args.input)
    if not sources:
        print(f"no audio files under {args.input}", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = args.out / "manifests" / "majel_compilation.jsonl"
    manifest.parent.mkdir(parents=True, exist_ok=True)

    print("loading models…", flush=True)
    enc, ref_emb = load_reference(args.reference)
    import whisper  # type: ignore
    whisper_model = whisper.load_model(args.whisper_model)

    total = 0
    for src in sources:
        print(f"[{src.name}] processing…", flush=True)
        try:
            kept = process(src, args.out, enc, ref_emb, whisper_model,
                           args.threshold, args.gap, args.silence_db,
                           args.min_dur, args.max_dur)
        except Exception as e:
            print(f"[{src.name}] FAILED: {e}", file=sys.stderr)
            continue
        with manifest.open("a") as f:
            for c in kept:
                f.write(json.dumps(asdict(c)) + "\n")
        total += len(kept)
        print(f"[{src.name}] kept {len(kept)} clips (running total: {total})")

    print(f"\nDone. {total} clips → {manifest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
