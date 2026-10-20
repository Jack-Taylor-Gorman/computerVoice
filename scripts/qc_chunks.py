#!/usr/bin/env python3
"""Post-render QC sweep for a multi_voice_read output dir.

Detects bad F5-TTS chunks (loops, hallucinations, drops, garbled onsets)
by transcribing each chunk with WhisperX and diffing against the
intended text in plan.jsonl, plus a handful of audio-domain signals.

Bad chunks are DELETED from chunks/. The caller (multi_voice_read.py
re-invoking its render_pending loop, or just re-running the script)
will re-synthesize only the missing ones, using a different seed
provided via the SEED_OFFSET in plan_overrides.json so the rerun isn't
just the same dice roll.

Flow:
  1. load plan.jsonl
  2. ASR every chunk_NNNN_<voice>.wav (WhisperX, batched)
  3. compute signals (WER, loop, length, audio stats)
  4. verdict: bad iff one hard-fail OR ≥2 soft from different families
  5. delete bad chunks, write qc_report_attempt{N}.jsonl + plan_overrides.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent

WER_SOFT, WER_HARD = 0.20, 0.30
LEN_SOFT = (0.75, 1.35)
LEN_HARD = (0.50, 1.60)
LOOP_1GRAM = 3    # same word ≥3× consecutively (was 4 — F5 commonly loops 3×)
LOOP_2GRAM = 2    # same bigram ≥2× in chunk (was 3 — catch shorter loops)
LOOP_3GRAM = 2    # same trigram ≥2× → almost always a real repeat
DUR_TOL = 0.40    # |dur - expected| / expected
SILENCE_HI = 0.55
SILENCE_LO_FLOOR_SEC = 2.0
SILENCE_LO = 0.02
RMS_SPIKE_DB = 12.0


def _norm(t: str) -> list[str]:
    t = re.sub(r"[^a-z0-9' ]", " ", t.lower())
    return [w for w in t.split() if w]


def wer(hyp: str, ref: str) -> float:
    h, r = _norm(hyp), _norm(ref)
    if not r:
        return 0.0
    # Levenshtein on word tokens
    m, n = len(r), len(h)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1): dp[i][0] = i
    for j in range(n + 1): dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if r[i - 1] == h[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                dp[i][j] = 1 + min(dp[i - 1][j], dp[i][j - 1], dp[i - 1][j - 1])
    return dp[m][n] / m


def loop_score(hyp: str, ref: str) -> tuple[str | None, int]:
    h = _norm(hyp); r = _norm(ref)
    if not h:
        return None, 0
    # 1-gram consecutive run
    run = best = 1
    run_word = h[0]
    for a, b in zip(h, h[1:]):
        if a == b:
            run += 1
            if run > best:
                best = run; run_word = a
        else:
            run = 1
    if best >= LOOP_1GRAM:
        # report a loop iff the source doesn't itself repeat that word as much
        ref_run = 1; ref_best = 1
        for a, b in zip(r, r[1:]):
            ref_run = ref_run + 1 if a == b else 1
            ref_best = max(ref_best, ref_run)
        if best > ref_best:
            return ("loop1", best)
    # 2-gram repeats
    bg = Counter(zip(h, h[1:]))
    for (a, b), c in bg.items():
        if c >= LOOP_2GRAM:
            ref_bg_count = Counter(zip(r, r[1:])).get((a, b), 0)
            if c > ref_bg_count:
                return ("loop2", c)
    # 3-gram repeats (trigram saying twice = almost certainly looped)
    if len(h) >= 6:
        tg = Counter(zip(h, h[1:], h[2:]))
        for tri, c in tg.items():
            if c >= LOOP_3GRAM:
                ref_tg_count = Counter(zip(r, r[1:], r[2:])).get(tri, 0)
                if c > ref_tg_count:
                    return ("loop3", c)
    return None, 0


def length_ratio(hyp: str, ref: str) -> float:
    h, r = _norm(hyp), _norm(ref)
    if not r:
        return 1.0
    return len(h) / len(r)


def audio_signals(wav_path: Path, plan_chars: int, speed: float) -> dict:
    audio, sr = sf.read(str(wav_path))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    dur = len(audio) / sr
    expected = max(0.3, (plan_chars / 14.0) * (0.7 / max(speed, 0.1)))
    dur_dev = abs(dur - expected) / expected
    win = max(1, int(0.05 * sr))
    n = len(audio) // win
    if n == 0:
        return dict(dur=dur, dur_dev=dur_dev,
                    silence_ratio=0.0, spike_db=0.0)
    frames = audio[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-10)
    db = 20 * np.log10(rms + 1e-10)
    silence_ratio = float(np.mean(db < -45))
    voiced = db[db > -45]
    spike_db = float(db.max() - voiced.mean()) if voiced.size else 0.0
    return dict(dur=float(dur), dur_dev=float(dur_dev),
                silence_ratio=silence_ratio, spike_db=spike_db)


def verdict(asr: str, plan_text: str, feats: dict) -> tuple[bool, dict]:
    w = wer(asr, plan_text)
    lp_kind, lp_n = loop_score(asr, plan_text)
    lr = length_ratio(asr, plan_text)
    # Tiny chunks (≤3 plan words) get a narrower check: duration and
    # silence-ratio flags are unreliable on <1s audio. Rely on WER+loop.
    plan_words = len(_norm(plan_text))
    is_tiny = plan_words <= 3

    hard, soft = [], []
    if w > WER_HARD:        hard.append(("wer_hard", w))
    if lp_kind:             hard.append((lp_kind, lp_n))
    if not (LEN_HARD[0] <= lr <= LEN_HARD[1]) and not is_tiny:
        hard.append(("len_hard", lr))

    if WER_SOFT < w <= WER_HARD:
        soft.append(("wer_soft", w))
    if not (LEN_SOFT[0] <= lr <= LEN_SOFT[1]) and not is_tiny:
        soft.append(("len_soft", lr))
    if not is_tiny and feats["dur_dev"] > DUR_TOL:
        soft.append(("dur_dev", feats["dur_dev"]))
    if not is_tiny and feats["silence_ratio"] > SILENCE_HI:
        soft.append(("silence_hi", feats["silence_ratio"]))
    if feats["dur"] > SILENCE_LO_FLOOR_SEC and feats["silence_ratio"] < SILENCE_LO:
        soft.append(("silence_lo", feats["silence_ratio"]))
    if feats["spike_db"] > RMS_SPIKE_DB:
        soft.append(("rms_spike", feats["spike_db"]))

    families = {tag.split("_")[0] for tag, _ in soft}
    bad = bool(hard) or len(families) >= 2
    info = {"wer": w, "len_ratio": lr,
            "hard": hard, "soft": soft,
            "plan_words": plan_words, "is_tiny": is_tiny,
            **feats}
    return bad, info


def transcribe(wav_paths: list[Path], model_size: str = None) -> dict[Path, str]:
    """Batch-transcribe with WhisperX. Returns {path: text}."""
    import whisperx
    size = model_size or os.environ.get("QC_WHISPER_MODEL", "medium")
    device = "cuda"
    compute_type = "float16"
    print(f"  loading WhisperX ({size})...", flush=True)
    asr = whisperx.load_model(size, device=device, compute_type=compute_type, language="en")
    out = {}
    for i, p in enumerate(wav_paths, 1):
        audio = whisperx.load_audio(str(p))
        r = asr.transcribe(audio, batch_size=8, language="en")
        text = " ".join(s["text"].strip() for s in r.get("segments", []))
        out[p] = text
        if i % 100 == 0:
            print(f"  ASR {i}/{len(wav_paths)}", flush=True)
    # free
    import gc, torch
    del asr; gc.collect(); torch.cuda.empty_cache()
    return out


def sweep(out_dir: Path, attempt: int) -> tuple[list[int], list[dict]]:
    plan_rows = [json.loads(l) for l in (out_dir / "plan.jsonl").read_text().splitlines() if l.strip()]
    plan_rows.sort(key=lambda r: r["seq"])
    plan = {r["seq"]: r for r in plan_rows}
    chunks_dir = out_dir / "chunks"
    wav_paths = []
    seq_for_path = {}
    for seq, row in plan.items():
        p = chunks_dir / f"chunk_{seq:04d}_{row['voice']}.wav"
        if p.exists():
            wav_paths.append(p)
            seq_for_path[p] = seq
    print(f"QC sweep #{attempt}: {len(wav_paths)} chunks on disk", flush=True)
    asr_results = transcribe(wav_paths)
    report = []
    bad = []
    seq_to_info = {}
    for p, asr_text in asr_results.items():
        seq = seq_for_path[p]
        row = plan[seq]
        feats = audio_signals(p, len(row.get("tts_text", row["text"])), float(row.get("speed", 0.7)))
        is_bad, info = verdict(asr_text, row.get("tts_text", row["text"]), feats)
        info["seq"] = seq
        info["voice"] = row["voice"]
        info["plan_text"] = row.get("tts_text", row["text"])
        info["asr"] = asr_text
        info["attempt"] = attempt
        info["bad"] = is_bad
        report.append(info)
        seq_to_info[seq] = info
        if is_bad:
            bad.append(seq)

    # Cross-chunk seam repeat detection: if chunk N's last 3 ASR words equal
    # chunk N+1's first 3 words AND those words don't legitimately repeat in
    # the plan text spanning both chunks, mark BOTH as bad. Catches F5
    # "echo" artefacts at voice changes that per-chunk QC misses.
    seqs_sorted = sorted(plan.keys())
    for a, b in zip(seqs_sorted, seqs_sorted[1:]):
        if a not in seq_to_info or b not in seq_to_info:
            continue
        ah = _norm(seq_to_info[a]["asr"])
        bh = _norm(seq_to_info[b]["asr"])
        if len(ah) < 2 or len(bh) < 2:
            continue
        # try 3-word overlap, fall back to 2
        for k in (3, 2):
            if len(ah) < k or len(bh) < k: continue
            tail = ah[-k:]; head = bh[:k]
            if tail != head: continue
            # check if this overlap is actually in the plan (legit repetition)
            combined_plan = _norm(seq_to_info[a]["plan_text"] + " " + seq_to_info[b]["plan_text"])
            # look for tail appearing twice consecutively or near-end+near-start
            joined = " ".join(combined_plan)
            phrase = " ".join(tail)
            if joined.count(phrase) >= 2:
                continue  # plan really does say it twice; not an artefact
            # mark both bad
            for seq in (a, b):
                if seq not in bad:
                    bad.append(seq)
                seq_to_info[seq].setdefault("hard", []).append(
                    ("seam_repeat", phrase))
                seq_to_info[seq]["bad"] = True
            break

    (out_dir / f"qc_report_attempt{attempt}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in report))
    return sorted(set(bad)), report


def stash_and_delete(out_dir: Path, plan: dict, bad_seqs: list[int], attempt: int) -> None:
    chunks_dir = out_dir / "chunks"
    stash_dir = out_dir / "qc_stash"
    stash_dir.mkdir(exist_ok=True)
    for seq in bad_seqs:
        voice = plan[seq]["voice"]
        wav = chunks_dir / f"chunk_{seq:04d}_{voice}.wav"
        if not wav.exists():
            continue
        wav.rename(stash_dir / f"attempt{attempt}_chunk_{seq:04d}_{voice}.wav")


def write_seed_overrides(out_dir: Path, bad_seqs: list[int], attempt: int) -> None:
    """Caller (multi_voice_read.py) reads this if --qc-seed-overrides is set."""
    path = out_dir / "seed_overrides.json"
    existing = {}
    if path.exists():
        try: existing = json.loads(path.read_text())
        except Exception: existing = {}
    for seq in bad_seqs:
        existing[str(seq)] = int(seq) + attempt * 100003
    path.write_text(json.dumps(existing, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", help="render output dir (containing plan.jsonl + chunks/)")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--delete-bad", action="store_true",
                    help="delete bad chunks and write seed_overrides.json")
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    if not (out_dir / "plan.jsonl").exists():
        print(f"no plan.jsonl in {out_dir}", file=sys.stderr); return 2
    bad, _ = sweep(out_dir, args.attempt)
    if not bad:
        print(f"✓ QC pass — no bad chunks")
        return 0
    print(f"⚠ {len(bad)} bad chunks: {bad[:20]}{'...' if len(bad) > 20 else ''}")
    if args.delete_bad:
        plan = {r["seq"]: r for r in
                (json.loads(l) for l in (out_dir / "plan.jsonl").read_text().splitlines() if l.strip())}
        stash_and_delete(out_dir, plan, bad, args.attempt)
        write_seed_overrides(out_dir, bad, args.attempt)
        print(f"  → stashed & deleted; seed overrides written to {out_dir/'seed_overrides.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
