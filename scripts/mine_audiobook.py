#!/usr/bin/env python3
"""Mine a single-narrator audiobook into sentence-aligned clips for F5-TTS.

Pivot from the multi-speaker Majel miner: an audiobook is one voice, clean
studio audio, with reliable punctuation. So this drops diarization and
embedding gating entirely. Pipeline:

  1. WhisperX large-v3 ASR + word-level alignment on the full audiobook.
  2. Greedy-pack words into 5–15s clips that close on sentence boundaries
     (`.`, `?`, `!`).
  3. ffmpeg cut at 24kHz mono PCM with small head/tail pads.
  4. Energy-based silence trim at clip edges to kill awkward pauses.
  5. Append to a JSONL manifest compatible with F5-TTS prepare_csv_wavs.

Resumable: WhisperX output is cached to dataset_avers/cache/asr.json. Pass
--overwrite to force a fresh transcription.

Usage:
  ./scripts/mine_audiobook.py \\
      --input dataset_avers/source/shadow_of_the_torturer.wav \\
      --out dataset_avers --device cuda:0
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Heavy imports (whisperx, soundfile, numpy) are lazy — keeps --help fast and
# lets the cache-only path run on a machine without a GPU stack.

SENTENCE_END = (".", "?", "!")
MIN_CLIP_S = 5.0
MAX_CLIP_S = 15.0
HARD_MIN_S = 4.0
HARD_MAX_S = 18.0
HEAD_PAD = 0.050
TAIL_PAD = 0.100
SAFETY_MARGIN = 0.030
MIN_TEXT_LEN = 8
MAX_TEXT_LEN = 280
LONG_GAP_S = 2.0  # force clip end on a silence longer than this


def run_whisperx(wav: Path, device: str) -> dict[str, Any]:
    """Transcribe + align. Returns {"segments": [...], "word_segments": [...]}.

    No diarization — single narrator. Mirrors mine_majel.py's import pattern.
    """
    import whisperx  # type: ignore

    model_size = os.environ.get("WHISPER_MODEL", "large-v3")
    dev = device.split(":")[0]
    compute_type = "float16" if dev == "cuda" else "int8"
    batch_size = 16 if dev == "cuda" else 4

    asr = whisperx.load_model(model_size, device=dev, compute_type=compute_type,
                              language="en")
    audio = whisperx.load_audio(str(wav))
    result = asr.transcribe(audio, batch_size=batch_size, language="en")

    align_model, metadata = whisperx.load_align_model(language_code="en", device=dev)
    aligned = whisperx.align(result["segments"], align_model, metadata, audio,
                             dev, return_char_alignments=False)
    return {
        "segments": aligned.get("segments", []),
        "word_segments": aligned.get("word_segments", []),
    }


def collect_words(asr: dict[str, Any], head_skip: float, tail_skip_from: float
                  ) -> list[dict[str, Any]]:
    """Flatten WhisperX segments to a single word list with start/end/word.

    Drops words missing timing (whisperx occasionally fails to align numerals
    and single letters) and words inside the head/tail skip zones.
    """
    words: list[dict[str, Any]] = []
    for seg in asr.get("segments", []):
        for w in seg.get("words", []) or []:
            tok = w.get("word") or w.get("text")
            s = w.get("start")
            e = w.get("end")
            if tok is None or s is None or e is None:
                continue
            s = float(s); e = float(e)
            if e <= s:
                continue
            if s < head_skip:
                continue
            if s >= tail_skip_from:
                continue
            words.append({"word": tok.strip(), "start": s, "end": e})
    words.sort(key=lambda w: w["start"])
    return words


def is_sentence_end(token: str) -> bool:
    """Token-final punctuation check. Approximate — abbreviations like 'Mr.'
    will sometimes split a clip early, but audiobook prose is clean enough
    that the loss is negligible."""
    t = token.rstrip("\"')]}")
    return bool(t) and t[-1] in SENTENCE_END


def pack_clips(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Greedy-pack words into 5–15s sentence-aligned clips.

    Rules:
      - Accumulate until duration >= MIN_CLIP_S, then close on next sentence
        end OR force-close if next word would push past MAX_CLIP_S.
      - Force-close on any inter-word gap > LONG_GAP_S (a long silence almost
        always means a paragraph or chapter break).
    """
    clips: list[dict[str, Any]] = []
    if not words:
        return clips

    buf: list[dict[str, Any]] = []
    clip_start = words[0]["start"]
    prev_end = words[0]["start"]

    def flush() -> None:
        if not buf:
            return
        s = buf[0]["start"]
        e = buf[-1]["end"]
        dur = e - s
        if HARD_MIN_S <= dur <= HARD_MAX_S:
            text = " ".join(w["word"] for w in buf).strip()
            clips.append({"start": s, "end": e, "duration": dur, "text": text})
        buf.clear()

    for w in words:
        gap = w["start"] - prev_end
        if buf and gap > LONG_GAP_S:
            flush()
            clip_start = w["start"]

        if not buf:
            clip_start = w["start"]
        buf.append(w)
        cur_dur = w["end"] - clip_start

        if cur_dur >= MIN_CLIP_S and is_sentence_end(w["word"]):
            flush()
        elif cur_dur >= MAX_CLIP_S:
            # Past the budget without a sentence end — close anyway. Discarded
            # by flush() if it overshot the hard max.
            flush()

        prev_end = w["end"]

    flush()
    return clips


def cut_clip(src: Path, start: float, end: float, dst: Path) -> bool:
    """ffmpeg cut to 24kHz mono PCM. Returns True on success."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = max(0.0, start - HEAD_PAD)
    dur = (end + TAIL_PAD) - s
    proc = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-ss", f"{s:.3f}", "-t", f"{dur:.3f}",
         "-i", str(src),
         "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", str(dst)],
        capture_output=True,
    )
    return proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def trim_silence(path: Path) -> float:
    """Trim leading/trailing low-energy regions in-place. Returns new duration.

    Why post-cut: sentence boundaries from ASR are word-end timestamps, which
    don't account for the natural breath/pause at sentence end. A short
    rolling-RMS sweep finds the actual speech bounds and tightens them up,
    leaving a 30ms safety margin so we don't clip consonants.

    Skips the trim if it would shorten the clip below HARD_MIN_S — better to
    keep an over-padded clip than to corrupt training.
    """
    import numpy as np
    import soundfile as sf

    audio, sr = sf.read(str(path))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype("float32")
    n = len(audio)
    if n < int(HARD_MIN_S * sr):
        return n / sr

    win = max(1, int(0.020 * sr))  # 20ms RMS window
    # Pad to a multiple of win, reshape, mean-square per frame.
    pad = (-n) % win
    if pad:
        audio_p = np.concatenate([audio, np.zeros(pad, dtype="float32")])
    else:
        audio_p = audio
    frames = audio_p.reshape(-1, win)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)

    overall = float(np.sqrt((audio ** 2).mean() + 1e-12))
    thresh = max(0.15 * overall, 1e-4)

    above = np.where(rms > thresh)[0]
    if len(above) == 0:
        return n / sr
    first = max(0, int(above[0] * win - SAFETY_MARGIN * sr))
    last = min(n, int((above[-1] + 1) * win + SAFETY_MARGIN * sr))
    new_dur = (last - first) / sr
    if new_dur < HARD_MIN_S:
        return n / sr

    sf.write(str(path), audio[first:last], sr, subtype="PCM_16")
    return new_dur


def fmt_hms(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path,
                    help="Source audiobook wav (mono, any sample rate).")
    ap.add_argument("--out", type=Path, default=Path("dataset_avers"),
                    help="Output root. Manifest goes to <out>/manifests/avers.jsonl")
    ap.add_argument("--device", default="cuda:0",
                    help="cuda:0 or cpu.")
    ap.add_argument("--head-skip", type=float, default=20.0,
                    help="Seconds to ignore at start (intro music/attribution).")
    ap.add_argument("--tail-skip", type=float, default=20.0,
                    help="Seconds to ignore at end (outro music/credits).")
    ap.add_argument("--max-hours", type=float, default=None,
                    help="Stop transcribing after this many hours (debug).")
    ap.add_argument("--limit-clips", type=int, default=None,
                    help="Stop after exporting this many kept clips (debug).")
    ap.add_argument("--overwrite", action="store_true",
                    help="Force re-run of WhisperX even if cache exists.")
    args = ap.parse_args()

    src: Path = args.input.resolve()
    out_root: Path = args.out.resolve()
    if not src.exists():
        print(f"input not found: {src}", file=sys.stderr)
        return 2

    out_root.mkdir(parents=True, exist_ok=True)
    cache_path = out_root / "cache" / "asr.json"
    clips_dir = out_root / "clips"
    manifest_path = out_root / "manifests" / "avers.jsonl"

    # --- ASR (cached) ---
    if cache_path.exists() and not args.overwrite:
        print(f"[asr] loading cache → {cache_path}", flush=True)
        asr = json.loads(cache_path.read_text())
    else:
        print(f"[asr] running WhisperX {os.environ.get('WHISPER_MODEL', 'large-v3')} on {src.name} (device={args.device})",
              flush=True)
        t0 = time.time()
        asr = run_whisperx(src, args.device)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(asr))
        print(f"[asr] done in {time.time() - t0:.1f}s, "
              f"{len(asr.get('segments', []))} segments cached", flush=True)

    # --- compute tail-skip-from ---
    import soundfile as sf
    info = sf.info(str(src))
    total_s = info.frames / info.samplerate
    if args.max_hours is not None:
        total_s = min(total_s, args.max_hours * 3600.0)
    tail_skip_from = max(args.head_skip, total_s - args.tail_skip)

    # --- pack clips ---
    words = collect_words(asr, args.head_skip, tail_skip_from)
    print(f"[pack] {len(words)} aligned words in window "
          f"[{args.head_skip:.0f}s, {tail_skip_from:.0f}s)", flush=True)
    candidates = pack_clips(words)
    print(f"[pack] {len(candidates)} candidate clips", flush=True)

    # --- export ---
    clips_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    kept = 0
    rejected = 0
    total_dur = 0.0
    durations: list[float] = []
    t_start = time.time()

    with manifest_path.open("a") as mf:
        for cand in candidates:
            text = cand["text"]
            if not (MIN_TEXT_LEN <= len(text) <= MAX_TEXT_LEN):
                rejected += 1
                continue

            idx = kept + 1
            name = f"avers__{idx:06d}.wav"
            dst = clips_dir / name
            if not cut_clip(src, cand["start"], cand["end"], dst):
                rejected += 1
                continue

            new_dur = trim_silence(dst)
            if new_dur < HARD_MIN_S or new_dur > HARD_MAX_S:
                dst.unlink(missing_ok=True)
                rejected += 1
                continue

            rec = {
                "audio_path": f"clips/{name}",
                "text": text,
                "start": round(cand["start"], 3),
                "end": round(cand["end"], 3),
                "duration": round(new_dur, 3),
            }
            mf.write(json.dumps(rec) + "\n")
            mf.flush()
            kept += 1
            total_dur += new_dur
            durations.append(new_dur)

            if kept % 50 == 0:
                elapsed = fmt_hms(time.time() - t_start)
                print(f"[{elapsed}] {kept} clips kept "
                      f"(duration so far: {total_dur / 60.0:.1f} min)", flush=True)

            if args.limit_clips is not None and kept >= args.limit_clips:
                print(f"[stop] hit --limit-clips {args.limit_clips}", flush=True)
                break

    # --- summary ---
    total_cands = len(candidates)
    mean_dur = (sum(durations) / len(durations)) if durations else 0.0
    kept_frac = (kept / total_cands) if total_cands else 0.0
    print()
    print(f"Done. {kept} clips, {total_dur / 60.0:.1f} min total, "
          f"mean {mean_dur:.2f}s, kept {kept_frac:.1%} "
          f"({rejected} rejected of {total_cands} candidates)")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
