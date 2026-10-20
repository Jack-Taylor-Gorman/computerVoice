#!/usr/bin/env python3
"""Read a long-form text file aloud in Severian's voice.

Pipes the text through the F5-TTS daemon at /tmp/severian_f5.sock (NOT
through severianize.py — we want a faithful narration, not a Severian-
style rewrite). Plays each chunk as it's synthesized, saves every chunk
to disk, concatenates per-segment WAVs, and emits one master WAV at the
end.

Defaults are tuned to the Avers training distribution: chunks ≈ 180
chars, segments ≈ 5000 chars (≈ 28 chunks per segment ≈ 4–5 min audio).

Output layout (under --out, default `dataset_avers/readings/<name>/`):

    source.txt                cleaned input
    manifest.jsonl            one row per chunk
    segments/segment_NNN/     per-chunk wavs
    segments/segment_NNN.wav  concatenated segment audio
    complete.wav              master concat of every segment

Usage:
    ./scripts/read_aloud.py \\
        /home/jackgorman/Downloads/epdf.pub_gene-wolfe-seven-american-nights.txt
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "dataset_avers" / "readings"
SOCK_CANDIDATES = ("/tmp/severian_f5.sock", "/tmp/majel_f5.sock")


def find_sock() -> str | None:
    for s in SOCK_CANDIDATES:
        if os.path.exists(s):
            return s
    return None


def _spell_number(n: int) -> str:
    """Spell an int as it should be SPOKEN. Year-like 4-digit numbers get
    the year reading ("nineteen seventy-eight"); everything else gets the
    cardinal reading."""
    try:
        from num2words import num2words  # type: ignore
    except ImportError:
        return str(n)
    if 1000 <= n <= 2999:
        return num2words(n, to="year")
    return num2words(n)


_NUMBER_RE = re.compile(r"\b(\d+)\b")


def _replace_numbers(text: str) -> str:
    return _NUMBER_RE.sub(lambda m: _spell_number(int(m.group(1))), text)


# Page-footer URL + bracketed date line. Often preceded by a form-feed.
# Pattern: optional \f, optional whitespace, "file://" through end of line.
# Also handles the bare URL on the next line that appears in this PDF→txt.
_PAGE_FOOTER_RE = re.compile(
    r"\f?\s*file://[^\n]*\n?", flags=re.MULTILINE,
)

# Soft-hyphen rejoins: PDF line breaks rendered a soft-hyphen as `=`, so
# `juxta= position` should be `juxtaposition`. Conservative — only joins
# when the `=` immediately follows a word char and the next non-space
# token is also word-initial.
_SOFT_HYPHEN_RE = re.compile(r"(\w+)=\s+(\w+)")

# Spaced-out single-letter corruption: `m y father` → `my father`.
# Conservative: only matches 2-3 consecutive single-letter words flanked
# by normal words/whitespace. Preserves the legitimate solo "I" / "a".
_SPACED_LETTERS_RE = re.compile(
    r"(?<![A-Za-z])((?:[a-zA-Z] ){1,3}[a-zA-Z])(?=[ ,.\";:!?])"
)

# Literal "\B0" / "\xb0" escape strings left in the text by the PDF→txt
# converter. Drop outright.
_LITERAL_ESCAPE_RE = re.compile(r"\\[xXbB][0-9A-Fa-f]+")

# Stray mid-clause colons: ". I could: escort" → "I could escort".
# Fires only when a colon sits between two lowercase words (not after
# proper noun + colon, not in dialogue tags like 'said: "...').
_STRAY_COLON_RE = re.compile(r"([a-z])\s*:\s+([a-z])")

# Parenthetical phonetic spelling: ('detartnent') — the narrator's
# transliteration of a character's slurred speech. Strip; the
# preceding correct word stays.
_PHONETIC_PAREN_RE = re.compile(r"\s*\('[a-zA-Z]+'\)")

# Underscore italics markers (e.g. `_special_` from the original
# typography) leak through the PDF→txt conversion. Drop the underscores
# but keep the word(s) between them.
_UNDERSCORE_PAIR_RE = re.compile(r"_([A-Za-z][^_]*[A-Za-z])_")
# Stray single underscores attached to a word boundary.
_UNDERSCORE_STRAY_RE = re.compile(r"(?<=\s)_+|_+(?=\s|[.,;:!?])")


def _fix_spaced_letters(match: re.Match) -> str:
    """Collapse 'm y ' → 'my '. Only fires on lowercase to avoid mangling
    legitimate acronyms like 'US A'."""
    seq = match.group(1).replace(" ", "")
    # Don't collapse if the result looks like a legitimate single word
    # already (e.g. 'I' on its own).
    return seq if len(seq) > 1 else match.group(1)


def clean_text(raw: str) -> str:
    """Aggressive text cleanup for fluent TTS.

    Strips the PDF→txt page-footer URL/date pattern that ran 25 times
    in our source, rejoins soft-hyphenated words (`juxta= position` →
    `juxtaposition`), fixes spaced-out letter corruption (`m y` → `my`),
    drops phonetic parentheticals like `('detartnent')` (the surrounding
    correct word is enough for the narrator), strips literal escape
    artifacts (`\\B0`), removes stray mid-clause colons, spells out
    numbers, normalises smart quotes / dashes, collapses whitespace.

    Keeps proper nouns, punctuation, and paragraph structure intact.
    """
    # CRLF → LF; strip form feeds (they pair with page-footer URLs).
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    # Literal \B0 / \xb0 escape strings the converter left in.
    raw = _LITERAL_ESCAPE_RE.sub("", raw)
    # Page-footer URL lines.
    raw = _PAGE_FOOTER_RE.sub("\n\n", raw)
    # Smart quotes → ASCII (TTS reads them better).
    raw = re.sub(r"[“”„]", '"', raw)
    raw = re.sub(r"[‘’‚]", "'", raw)
    # Em/en/figure dashes → comma pauses.
    raw = re.sub(r"\s*[—–−]\s*", ", ", raw)
    # Phonetic parentheticals — the narrator's transliteration of slurred
    # speech, e.g. `("detartnent")`. The previous word is correct; drop.
    raw = _PHONETIC_PAREN_RE.sub("", raw)
    # Italics underscores → just the word.
    raw = _UNDERSCORE_PAIR_RE.sub(r"\1", raw)
    raw = _UNDERSCORE_STRAY_RE.sub("", raw)
    # Stray mid-clause colons.
    raw = _STRAY_COLON_RE.sub(r"\1 \2", raw)
    # Soft-hyphen rejoins.
    raw = _SOFT_HYPHEN_RE.sub(r"\1\2", raw)
    # Spaced-letter corruption — run twice in case multiple windows
    # overlapped in a single span.
    raw = _SPACED_LETTERS_RE.sub(_fix_spaced_letters, raw)
    raw = _SPACED_LETTERS_RE.sub(_fix_spaced_letters, raw)
    # Spell out numbers.
    raw = _replace_numbers(raw)
    # Paragraph-aware whitespace collapse.
    paragraphs = re.split(r"\n\s*\n", raw)
    cleaned = []
    for p in paragraphs:
        p = re.sub(r"\s+", " ", p).strip()
        if p:
            cleaned.append(p)
    return "\n\n".join(cleaned)


def split_sentences(text: str) -> list[str]:
    """Sentence-split that doesn't choke on abbreviations like "Mr." / "Dr.".
    Best-effort; we only need rough sentence boundaries for chunking."""
    abbrev = re.compile(
        r"\b(?:Mr|Mrs|Ms|Dr|St|Sr|Jr|vs|etc|e\.g|i\.e|cf|Capt|Lt|Col|Gen|"
        r"Rev|Prof|Sgt|Pvt|Cpl|Mt|Ft|Co|Ltd|Inc)\.",
        re.IGNORECASE,
    )
    masked = abbrev.sub(lambda m: m.group(0).replace(".", "§"), text)
    # Split on sentence terminators followed by whitespace + capital letter.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", masked)
    return [p.replace("§", ".").strip() for p in parts if p.strip()]


def chunk_for_tts(text: str, max_chars: int) -> list[str]:
    """Greedy-pack sentences into chunks <= max_chars. A single oversized
    sentence is split on commas; if still too long, hard-split mid-clause."""
    out: list[str] = []
    buf = ""
    for sent in split_sentences(text):
        if len(sent) > max_chars:
            # Try comma splits.
            parts = re.split(r",\s+", sent)
            sub_buf = ""
            for p in parts:
                if not p:
                    continue
                if len(sub_buf) + len(p) + 2 <= max_chars:
                    sub_buf = (sub_buf + ", " + p).lstrip(", ")
                else:
                    if sub_buf:
                        out.append(sub_buf)
                    if len(p) > max_chars:
                        # Hard wrap.
                        for i in range(0, len(p), max_chars):
                            out.append(p[i:i + max_chars])
                        sub_buf = ""
                    else:
                        sub_buf = p
            if sub_buf:
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(sub_buf)
            continue
        if len(buf) + len(sent) + 1 <= max_chars:
            buf = (buf + " " + sent).strip()
        else:
            if buf:
                out.append(buf)
            buf = sent
    if buf:
        out.append(buf)
    return out


def split_into_segments(text: str, target_chars: int) -> list[list[str]]:
    """Group paragraphs into segments of ~target_chars each, breaking on
    paragraph boundaries. Each segment becomes its own audio file."""
    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    segments: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for p in paragraphs:
        if cur_len + len(p) > target_chars and cur:
            segments.append(cur)
            cur = [p]
            cur_len = len(p)
        else:
            cur.append(p)
            cur_len += len(p) + 2
    if cur:
        segments.append(cur)
    return segments


def synth(sock: str, text: str, dst: str, timeout: float = 180.0) -> bool:
    """Send synthesis request to the F5 daemon."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(sock)
        s.sendall(json.dumps({"text": text, "dst": dst}).encode() + b"\n")
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.close()
        resp = json.loads(buf.decode() or '{"ok": false}')
        return bool(resp.get("ok"))
    except (OSError, json.JSONDecodeError):
        return False


def ffmpeg_concat(inputs: list[Path], out: Path) -> bool:
    """Concatenate WAV files via ffmpeg's concat demuxer."""
    if not inputs:
        return False
    list_file = out.with_suffix(".concat.txt")
    with list_file.open("w") as f:
        for p in inputs:
            f.write(f"file '{p.resolve()}'\n")
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error",
         "-f", "concat", "-safe", "0", "-i", str(list_file),
         "-c:a", "pcm_s16le", "-ar", "24000", "-ac", "1", str(out)],
    )
    list_file.unlink(missing_ok=True)
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="path to a UTF-8 / Latin-1 text file")
    ap.add_argument("--out", default=None,
                    help=f"output dir (default: {DEFAULT_OUT}/<stem>)")
    ap.add_argument("--segment-chars", type=int, default=5000,
                    help="approximate chars per segment (default 5000 ≈ 4–5 min audio)")
    ap.add_argument("--chunk-chars", type=int, default=180,
                    help="max chars per F5 synthesis chunk (default 180 ≈ 8–10 s "
                         "audio, matches the Avers training distribution)")
    ap.add_argument("--no-play", action="store_true",
                    help="export only, don't paplay each chunk as it's synthesized")
    ap.add_argument("--start-segment", type=int, default=1,
                    help="skip to segment N (resume after an interrupt)")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"missing input: {src}", file=sys.stderr)
        return 2

    sock = find_sock()
    if not sock:
        print(f"F5 daemon socket not found at any of {SOCK_CANDIDATES}",
              file=sys.stderr)
        print("Start the daemon first: bash run_f5_daemon.sh &", file=sys.stderr)
        return 2

    raw = src.read_text(encoding="latin-1", errors="replace")
    cleaned = clean_text(raw)

    out_root = Path(args.out) if args.out else (DEFAULT_OUT / src.stem)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "source.txt").write_text(cleaned, encoding="utf-8")

    segments = split_into_segments(cleaned, args.segment_chars)
    print(f"input: {src}")
    print(f"output: {out_root}")
    print(f"daemon: {sock}")
    print(f"clean text: {len(cleaned)} chars in {len(cleaned.split())} words "
          f"across {sum(1 for _ in cleaned.split(chr(10)+chr(10)))} paragraphs")
    print(f"segments: {len(segments)} (~{args.segment_chars} chars each)")
    print()

    manifest_path = out_root / "manifest.jsonl"
    manifest_f = manifest_path.open("a")
    segments_dir = out_root / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    # Pipelined synthesis + playback. The producer thread synthesises the
    # next chunk while the main thread plays the previous one.
    play_q: queue.Queue[Path | None] = queue.Queue(maxsize=2)

    def player():
        while True:
            item = play_q.get()
            if item is None:
                return
            if not args.no_play:
                subprocess.run(["paplay", str(item)],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)

    player_t = threading.Thread(target=player, daemon=True)
    player_t.start()

    all_segment_wavs: list[Path] = []
    total_chunks = 0
    t_start = time.monotonic()

    for seg_idx, paragraphs in enumerate(segments, start=1):
        seg_name = f"segment_{seg_idx:03d}"
        seg_dir = segments_dir / seg_name
        seg_wav = segments_dir / f"{seg_name}.wav"
        all_segment_wavs.append(seg_wav)
        if seg_idx < args.start_segment:
            print(f"[skip] {seg_name} (start at {args.start_segment})")
            continue
        seg_dir.mkdir(parents=True, exist_ok=True)
        seg_text = " ".join(paragraphs)
        chunks = chunk_for_tts(seg_text, args.chunk_chars)
        print(f"[{seg_name}] {len(chunks)} chunks, {len(seg_text)} chars")
        chunk_wavs: list[Path] = []
        for ci, ctext in enumerate(chunks, start=1):
            dst = seg_dir / f"chunk_{ci:04d}.wav"
            was_cached = False
            if dst.exists() and dst.stat().st_size > 0:
                print(f"  [cached] chunk {ci}/{len(chunks)}", flush=True)
                was_cached = True
            else:
                t_synth = time.monotonic()
                ok = synth(sock, ctext, str(dst))
                d = time.monotonic() - t_synth
                if not ok:
                    print(f"  [fail] chunk {ci}/{len(chunks)} — {ctext[:60]!r}",
                          file=sys.stderr, flush=True)
                    continue
                print(f"  chunk {ci}/{len(chunks)} ({d:.1f}s synth) → {ctext[:60]!r}",
                      flush=True)
            chunk_wavs.append(dst)
            manifest_f.write(json.dumps({
                "segment": seg_idx,
                "chunk": ci,
                "wav": str(dst.relative_to(out_root)),
                "text": ctext,
            }) + "\n")
            manifest_f.flush()
            # Only queue for playback if we just synthesized — never replay
            # the entire cache on resume.
            if not was_cached:
                play_q.put(dst)
            total_chunks += 1

        # Concat the segment once all chunks are queued (don't block on
        # playback — concat is a separate process, playback continues).
        if chunk_wavs:
            if ffmpeg_concat(chunk_wavs, seg_wav):
                print(f"  → {seg_wav.name} ({seg_wav.stat().st_size // 1024} KB)")
            else:
                print(f"  [warn] concat failed for {seg_name}", file=sys.stderr)

    # Wait for playback to drain.
    play_q.put(None)
    player_t.join(timeout=300)

    # Master concat across all segments.
    valid_segments = [p for p in all_segment_wavs if p.exists()]
    if len(valid_segments) > 1:
        master = out_root / "complete.wav"
        if ffmpeg_concat(valid_segments, master):
            sz = master.stat().st_size / 1024 / 1024
            print(f"\nmaster: {master} ({sz:.1f} MB)")
        else:
            print(f"\n[warn] master concat failed", file=sys.stderr)

    manifest_f.close()
    elapsed = time.monotonic() - t_start
    print(f"\ndone — {total_chunks} chunks in {elapsed/60:.1f} min")
    return 0


if __name__ == "__main__":
    sys.exit(main())
