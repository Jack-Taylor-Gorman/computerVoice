#!/usr/bin/env python3
"""Build a subtitled video from a rendered audiobook wav.

Pipeline:
  1. Run WhisperX on the rendered audio to get word-level timestamps
  2. Group words into ~2-line subtitle "windows" by sentence boundaries
  3. Emit a .ass subtitle file with two-line sliding karaoke style:
       - current sentence line on top (larger, bright)
       - next sentence line below (smaller, dim)
       - active word within current sentence highlighted via \\k tags
  4. ffmpeg-compose a 1920x1080 black video with the audio + burned-in subs
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ── Subtitle styling (in .ass format) ───────────────────────────────────
# Two styles: "Now" (current sentence, large+bright) and "Next" (upcoming, dim).
SCRIPT_INFO = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Now,    Inter,52,&H00FFFFFF,&H0080FFFF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3.5,1.5,5,80,80,30,1
Style: Next,   Inter,36,&H80B0B0B0,&H80808080,&H80000000,&H80000000,0,0,0,0,100,100,0,0,1,2.5,0,5,80,80,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# Style fields tuned for: Now = white + bold + cyan karaoke highlight (SecondaryColour),
# Next = dim grey, slightly transparent (alpha=80 in ABGR).
# Alignment 5 = top-center; we'll use MarginV to position vertically.


def ass_ts(t: float) -> str:
    """seconds → H:MM:SS.cc"""
    h = int(t // 3600); m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def whisperx_align(wav_path: Path, asr_text: str | None = None,
                   device: str = "cuda:0",
                   force_segments: list[dict] | None = None) -> list[dict]:
    """Get word-level timestamps for wav_path.

    If `force_segments` is provided (each {start, end, text}), we skip the
    ASR transcription step entirely and run only the alignment model with
    the supplied canonical text. This is what we want when we already
    have the rendered audio's source text (from plan.jsonl) — it
    guarantees the subtitles match the book, not Whisper's hallucinations.

    Otherwise we fall back to free transcription + alignment.
    """
    import os, whisperx
    audio = whisperx.load_audio(str(wav_path))
    dev = device.split(":")[0]

    if force_segments is not None:
        print(f"  force-aligning {len(force_segments)} segments...", flush=True)
        align_model, metadata = whisperx.load_align_model(
            language_code="en", device=dev)
        aligned = whisperx.align(force_segments, align_model, metadata,
                                 audio, dev, return_char_alignments=False)
        return aligned.get("word_segments", [])

    model_size = os.environ.get("WHISPER_MODEL", "large-v3")
    print(f"  loading WhisperX {model_size}...", flush=True)
    compute_type = "float16" if device.startswith("cuda") else "int8"
    asr = whisperx.load_model(model_size, device=dev,
                              compute_type=compute_type, language="en")
    print(f"  transcribing {wav_path.name}...", flush=True)
    result = asr.transcribe(audio, batch_size=16, language="en")
    print("  aligning...", flush=True)
    align_model, metadata = whisperx.load_align_model(
        language_code="en", device=dev)
    aligned = whisperx.align(result["segments"], align_model, metadata, audio,
                             dev, return_char_alignments=False)
    return aligned.get("word_segments", [])


def build_force_segments(plan_path: Path, chunks_dir: Path) -> list[dict] | None:
    """Build alignment segments from plan.jsonl + per-chunk wav durations.
    Returns None if plan.jsonl is missing.

    Voice-change silence: ffmpeg_concat in multi_voice_read inserts an
    extra silent wav between consecutive chunks whose voice differs.
    The master audio is therefore LONGER than the sum of chunk durations,
    by N * VOICE_CHANGE_SILENCE_SEC. We must account for this offset or
    subtitles drift forward as the audio progresses.
    """
    if not plan_path.exists():
        return None
    # Mirror of multi_voice_read.VOICE_CHANGE_SILENCE_SEC — kept in sync
    # by hand. Read it from there if you change the constant.
    VOICE_CHANGE_SILENCE_SEC = 0.65
    rows = [json.loads(l) for l in plan_path.read_text().splitlines() if l.strip()]
    segments = []
    cursor = 0.0
    prev_voice = None
    for r in rows:
        # chunk file matches: chunk_NNNN_<voice>.wav
        seq = r["seq"]; voice = r["voice"]
        wav = chunks_dir / f"chunk_{seq:04d}_{voice}.wav"
        if not wav.exists():
            print(f"  missing chunk wav: {wav}", file=sys.stderr); continue
        dur = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(wav)]).strip())
        sil = 0.0
        if prev_voice is not None and voice != prev_voice:
            sil = max(sil, VOICE_CHANGE_SILENCE_SEC)
        extra = float(r.get("extra_silence_before", 0) or 0)
        if extra > 0:
            sil = max(sil, extra)
        cursor += sil
        segments.append({
            "start": cursor,
            "end": cursor + dur,
            "text": r["text"],  # display text — what the book says
        })
        cursor += dur
        prev_voice = voice
    print(f"  built {len(segments)} force-align segments from plan.jsonl")
    return segments


def group_into_sentences(words: list[dict]) -> list[list[dict]]:
    """Group consecutive words into sentence-bounded subtitle windows.
    Cap each window at ~8 seconds AND on sentence-ending punctuation."""
    out = []
    cur = []
    cur_start = None
    for w in words:
        if "start" not in w or "end" not in w:
            continue
        text = w.get("word", "").strip()
        if not text:
            continue
        if cur_start is None:
            cur_start = w["start"]
        cur.append(w)
        ends_sentence = bool(re.search(r"[.!?]$", text))
        too_long = (w["end"] - cur_start) > 8.0
        too_many = len(cur) > 14
        if ends_sentence or too_long or too_many:
            out.append(cur)
            cur = []
            cur_start = None
    if cur:
        out.append(cur)
    return out


def build_ass(words: list[dict], out_path: Path) -> int:
    """Emit a .ass file with sliding 2-line karaoke subs. Returns
    number of events written."""
    sentences = group_into_sentences(words)
    if not sentences:
        return 0

    lines = [SCRIPT_INFO]
    n_events = 0

    for i, sent in enumerate(sentences):
        s_start = sent[0]["start"]
        s_end = sent[-1]["end"]
        # Build karaoke-tagged text for the current sentence (Now style).
        # \\k duration is in centiseconds; we tag each word with how long
        # *that word* takes from its start to the next word's start.
        chunks = []
        for j, w in enumerate(sent):
            text = w.get("word", "").strip()
            if not text:
                continue
            next_start = sent[j + 1]["start"] if j + 1 < len(sent) else w["end"]
            dur_cs = max(1, int((next_start - w["start"]) * 100))
            chunks.append(rf"{{\kf{dur_cs}}}{text} ")
        now_text = "".join(chunks).rstrip()

        # The "Next" preview is the following sentence as plain text.
        next_text = ""
        if i + 1 < len(sentences):
            next_text = " ".join(w["word"] for w in sentences[i + 1]).strip()

        lines.append(
            f"Dialogue: 0,{ass_ts(s_start)},{ass_ts(s_end)},Now,,0,0,420,,{now_text}"
        )
        n_events += 1
        if next_text:
            lines.append(
                f"Dialogue: 0,{ass_ts(s_start)},{ass_ts(s_end)},Next,,0,0,520,,{next_text}"
            )
            n_events += 1

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return n_events


def compose_video(wav_path: Path, ass_path: Path, out_path: Path,
                  width: int = 1920, height: int = 1080) -> None:
    """Burn the .ass subs onto a black video matching the audio length."""
    # Use lavfi color source for black video + the wav as audio + subtitles filter.
    # libx264 with -tune stillimage (we're mostly static) keeps file size small.
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "warning",
         "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=24",
         "-i", str(wav_path),
         "-vf", f"subtitles={ass_path}",
         "-c:v", "libx264", "-preset", "medium", "-crf", "23",
         "-tune", "stillimage",
         "-c:a", "aac", "-b:a", "192k",
         "-shortest",
         str(out_path)],
        check=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", help="path to rendered complete.wav")
    ap.add_argument("--out", default=None,
                    help="output mp4 (default: <audio_dir>/<stem>_subtitled.mp4)")
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--ass-only", action="store_true",
                    help="produce .ass subtitle file only, skip video render")
    args = ap.parse_args()

    audio = Path(args.audio)
    if not audio.exists():
        print(f"missing: {audio}", file=sys.stderr); return 2
    out_video = Path(args.out) if args.out else audio.parent / f"{audio.stem}_subtitled.mp4"
    out_ass = audio.parent / f"{audio.stem}.ass"
    out_words = audio.parent / f"{audio.stem}.words.json"

    print(f"audio: {audio}")
    print(f"out:   {out_video}")

    # 1) WhisperX — prefer force-alignment against plan.jsonl if present
    if out_words.exists():
        words = json.loads(out_words.read_text())
        print(f"  loaded {len(words)} cached word timestamps")
    else:
        plan_path = audio.parent / "plan.jsonl"
        chunks_dir = audio.parent / "chunks"
        force_segs = build_force_segments(plan_path, chunks_dir) \
            if plan_path.exists() and chunks_dir.exists() else None
        words = whisperx_align(audio, device=args.device,
                               force_segments=force_segs)
        out_words.write_text(json.dumps(words))
        print(f"  cached {len(words)} word timestamps → {out_words}")

    # 2) Build subtitles
    n = build_ass(words, out_ass)
    print(f"  wrote {n} subtitle events → {out_ass}")

    if args.ass_only:
        print("\n--ass-only set; skipping video render")
        return 0

    # 3) Compose video
    print("  composing video (this may take a while)...")
    compose_video(audio, out_ass, out_video)
    sz = out_video.stat().st_size / 1024 / 1024
    print(f"\n✓ wrote {out_video} ({sz:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
