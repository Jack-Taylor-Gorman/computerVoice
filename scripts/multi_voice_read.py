#!/usr/bin/env python3
"""Read a multi-character story aloud, routing each dialogue line to a
character-specific F5-TTS finetune and the narration to the narrator
finetune. Plays each segment as it's synthesized, and concatenates a
single complete.wav at the end.

Inputs:
  - cleaned book text
  - attribution.jsonl from scripts/attribute_speakers.py
  - per-speaker → trained-voice mapping (hardcoded below; edit per story)

Strategy:
  1. Parse text into segments: (narration_text | dialogue_span_text, speaker, voice).
  2. Chunk each segment for TTS (≤180 chars).
  3. Group consecutive chunks by voice so each F5 model loads once.
  4. Synthesize each voice's chunks, save with sequential index.
  5. Play in order while subsequent synth continues.
  6. ffmpeg-concat at the end into complete.wav.
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import gc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── Voice mapping ─────────────────────────────────────────────────────
# (story_speaker_label → (run_name, ckpt_stem, ref_clip_path, ref_text))
# Update this map per-story.

# Hand-picked reference clips per character voice. Overrides the
# auto-scorer below, which optimizes for calmness/comma-count but
# misses role/affect fit (e.g. picks an angry clip for a calm priest).
# Each entry: voice_dir → (clip_filename, transcript).
REF_CLIP_OVERRIDES = {
    # Silk — calm young priest; introspective multi-clause reflection
    "severian-character": ("b3_clip_0015.wav",
        "I could see he was lying, but I could also see that he was lying "
        "for what he believed to be my good, and so I said,"),
    # Blood / Auk — smooth menace dropped-g dialect for Auk; Blood gets pitch
    "baldanders": ("clip_0003.wav",
        "just as I told you, Liege, Madame, nineteen times of a score "
        "there's nothin' to it. We've only to get her over the wall now."),
    # Crane / Musk — both routed through dr-talos with differential pitch
    "dr-talos": ("clip_0064.wav",
        "I think the angel of agony there, and your understudy, will "
        "remain with us a while longer."),
    # Maytera Marble / Chenille — gracious-hostess tone (weak fit for Marble's age)
    "thecla": ("clip_0014.wav",
        "If this were my suite in the House Absolute, I could offer you "
        "better comfort. Unfortunately, you never called while I was there."),
    # Horn / Feather / Mint — gentlest reflective line in a tiny pool
    "dorcas": ("b3_clip_0003.wav",
        "You mean that it would be better for me to live somewhere else, "
        "and only come to see you once or twice a week, or wait till you "
        "came to see me."),
    # Orchid / Rose / Mucor — knowing-madam tone, jaded
    "agia": ("clip_0086.wav",
        "It will only lead to such a house as was used in the hot parts "
        "of the world in ancient days. You'll soon be bored, believe me."),
}


def char_ref(char):
    """Pick the CALMEST ~5-8s ref clip from a character's bucket.

    F5-TTS copies the reference clip's expressiveness onto every line of
    synth, so a ranting ref turns calm narration into a rant. We score
    each candidate by punctuation balance: periods (+1), exclamations (-2),
    questions (-1). The clip with the highest score wins."""
    # Hand-picked override wins over the scorer.
    if char in REF_CLIP_OVERRIDES:
        clip, text = REF_CLIP_OVERRIDES[char]
        path = ROOT / f"dataset_avers/characters_merged/{char}/{clip}"
        if path.exists():
            return path, " ".join(text.split())

    man = ROOT / f"dataset_avers/characters_merged/{char}/manifest.jsonl"
    if not man.exists():
        return None, None

    # Proper nouns that F5 reliably hallucinates from the reference clip
    # into generated lines (e.g. Thecla's clip ending "...Vodalus." caused
    # Maytera Marble's lines to loop "Vodalus" mid-sentence). Reject any
    # candidate ref whose transcript contains one of these.
    CONTAM = {"vodalus", "severian", "thecla", "talos", "baldanders",
              "autarch", "agia", "agilus", "dorcas", "jolenta",
              "abaia", "erebus", "abaian"}
    def calm_score(r):
        text = r.get("text", "") or ""
        dur = r.get("duration") or 0
        if dur < 5.0 or dur > 9.0:
            return -1
        if len(text) < 50:
            return -1
        low = text.lower()
        for name in CONTAM:
            if re.search(rf"\b{name}\b", low):
                return -1
        return text.count(".") - 2 * text.count("!") - text.count("?")

    candidates = []
    for line in man.read_text().splitlines():
        r = json.loads(line)
        score = calm_score(r)
        if score >= 0:
            candidates.append((score, r))
    if not candidates:
        return None, None
    candidates.sort(key=lambda x: -x[0])
    _, best = candidates[0]
    path = ROOT / f"dataset_avers/characters_merged/{char}/{best['clip']}"
    text = " ".join(best["text"].split())
    if path.exists() and text:
        return path, text
    return None, None


def narrator_ref():
    # Reference clip chosen for its multi-comma cadence: F5 copies the
    # prosodic pattern of the reference, so a comma-rich ref teaches it
    # to honor commas as audible breaths instead of running clauses
    # together. Old ref ("They'd send to Master Gurlow's…") had no
    # commas → F5 ignored commas in generated text.
    path = ROOT / "dataset_avers/clips_curated/000080__0.971__6.5s__Meanwhile_Nessus_the_city_imperishable_the.wav"
    text = ("Meanwhile, Nessus, the city imperishable, the city in which I had "
            "lived all my life, though I had seen so little of it, lay all about me.")
    return path, text


SPEAKER_TO_VOICE = {
    # story label → (voice_label, run, ckpt, ref-finder)
    "rab":                ("baldanders", "avers_baldanders",          "model_last", "baldanders"),
    "bruce":              ("dr_talos",   "avers_dr_talos",            "model_last", "dr-talos"),
    "david":              ("dr_talos",   "avers_dr_talos",            "model_last", "dr-talos"),
    "hah":                ("dr_talos",   "avers_dr_talos",            "model_last", "dr-talos"),
    "severian-character": ("severian-c", "avers_severian_character",  "model_last", "severian-character"),
    "severian-narrator":  ("narrator",   "avers_run4",                "model_1600", None),

    # ── Long Sun casting (Book of the Long Sun, Nightside) ───────────
    # No Long Sun voices exist (no audiobook to mine), so its characters
    # are cast against the 7 New Sun finetunes. Voice assignment is
    # driven by scene co-occurrence: characters who share scenes get
    # distinct voices; characters who never co-occur can share.
    "silk":           ("severian-c", "avers_severian_character", "model_last", "severian-character"),
    "patera-silk":    ("severian-c", "avers_severian_character", "model_last", "severian-character"),
    "patera":         ("severian-c", "avers_severian_character", "model_last", "severian-character"),
    "blood":          ("baldanders", "avers_baldanders",         "model_last", "baldanders"),
    "musk":           ("dr_talos",   "avers_dr_talos",           "model_last", "dr-talos"),
    "crane":          ("dr_talos",   "avers_dr_talos",           "model_last", "dr-talos"),
    "maytera-marble": ("thecla",     "avers_thecla",             "model_last", "thecla"),
    "marble":         ("thecla",     "avers_thecla",             "model_last", "thecla"),
    "maytera-rose":   ("agia",       "avers_agia",               "model_400",  "agia"),
    "rose":           ("agia",       "avers_agia",               "model_400",  "agia"),
    "maytera":        ("thecla",     "avers_thecla",             "model_last", "thecla"),
    "maytera-mint":   ("dorcas",     "avers_dorcas",             "model_last", "dorcas"),
    "mint":           ("dorcas",     "avers_dorcas",             "model_last", "dorcas"),
    "horn":           ("dorcas",     "avers_dorcas",             "model_last", "dorcas"),
    "feather":        ("dorcas",     "avers_dorcas",             "model_last", "dorcas"),
    "pike":           ("baldanders", "avers_baldanders",         "model_last", "baldanders"),
    "auk":            ("baldanders", "avers_baldanders",         "model_last", "baldanders"),
    "orchid":         ("agia",       "avers_agia",               "model_400",  "agia"),
    "chenille":       ("thecla",     "avers_thecla",             "model_last", "thecla"),
    "mucor":          ("agia",       "avers_agia",               "model_400",  "agia"),
    # anything not in this map → narrator
}

NARRATOR_VOICE = SPEAKER_TO_VOICE["severian-narrator"]

# Pronunciation overrides for F5. The "tts" form is fed to the model; the
# "display" form is what the subtitles show. Add phrase-level entries when
# F5 reads a heteronym wrong. Keep them narrow (full phrase) so we don't
# accidentally rewrite the same word in a different sense elsewhere.
TTS_PRONOUNCE_OVERRIDES = [
    # "the mammoth lives" — verb form, /lɪvz/ like "gives" not /laɪvz/
    ("the mammoth lives", "the mammoth livz"),
    # "one eyed" — clean_text removes the compound hyphen, leaving F5 to
    # garble "one eyed" into "sick" or similar. Force phonetic spelling.
    ("one eyed men",     "wun-eyed men"),
    ("one eyed thieves", "wun-eyed thieves"),
    ("one eyed",         "wun-eyed"),
    # "Hah" — F5 sometimes spells it out as letters (H-A-H). Force the
    # single-syllable pronunciation by using "Ha".
    ("Hah", "Ha"),
    # "could use one" — verb-form /juːz/ (as in "yooss"). Subtitle keeps
    # the canonical English spelling; only F5 sees the phonetic form.
    ("could use one", "could yoose one"),
]


def apply_tts_overrides(text: str) -> str:
    out = text
    for display, tts in TTS_PRONOUNCE_OVERRIDES:
        out = out.replace(display, tts)
    # F5 reads brackets/parens as literal pauses or mis-tokenizes the
    # following word's onset (the "weird artifact when an opening paren
    # appears" bug). Strip them from gen_text but leave the display
    # string alone so subtitles still show them.
    out = re.sub(r"[\(\)\[\]\{\}]", " ", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out

# Per-chunk filter chain: spectral shaping + COMPRESSOR but NO loudnorm.
# The compressor tames excited peaks (e.g. Dr Talos's "Hah!" yells) without
# introducing AGC drift; loudnorm WAS the seam-dip culprit and is now
# applied only once over the master at the end.
EQ = ("equalizer=f=120:t=q:w=1.0:g=2.5,"
      "equalizer=f=5000:t=q:w=1.2:g=-4,"
      "equalizer=f=8000:t=q:w=1.2:g=-2,"
      "lowpass=f=11000:width_type=q:width=0.7,"
      "acompressor=threshold=-22dB:ratio=2:attack=20:release=200,"
      # 80ms of silence padding at the END of each chunk. Without this,
      # back-to-back voice-switch transitions can clip the trailing
      # syllable of the prior chunk because F5's output sometimes ends
      # right on the final phoneme.
      # 40ms silence pad — small enough that consecutive chunks of the
      # same voice flow into each other naturally, large enough to give
      # voice transitions a hint of breath.
      # 220ms inter-chunk pad: gives sentence/clause-end chunks an
      # audible breath. F5 ignores in-text commas if the reference clip
      # has none, so we lean on chunk seams + a comma-rich reference
      # clip to restore natural prosody.
      "apad=pad_dur=0.22")

# Master pass: ONE loudnorm at the very end, over the whole concatenated
# output. Prevents per-chunk AGC drift while still hitting a consistent
# overall LUFS target.
# 60ms fade-in at the start of the master removes any ghost-syllable
# F5 onset that slipped past the per-chunk silenceremove (the "audio
# artifact at the beginning" symptom).
MASTER_DYNAMICS = ("afade=t=in:st=0:d=0.06,"
                   "loudnorm=I=-18:TP=-1.5:LRA=11")

CHUNK_CHARS = 180  # F5 stability comes from seeded sampling, not tiny
                    # chunks. Smaller chunks (120) make seams too dense
                    # and pauses feel chopped, while every additional
                    # seam is another opportunity for F5 to gamble.

# Per-speaker pitch shift (semitones). F5 can't change a voice's
# fundamental pitch via prompting, so we post-shift via asetrate/atempo
# in synth_chunk. Negative = deeper.
SPEAKER_PITCH = {
    "blood": -2.0,   # baldanders is already deep; small extra dip for menace
    "crane": +1.5,   # lift dr_talos to differentiate from Musk (same voice)
    "auk":   -1.5,   # nudge below Blood since they share baldanders
}


# ── Text helpers ──────────────────────────────────────────────────────

def split_sentences(text):
    abbrev = re.compile(
        r"\b(?:Mr|Mrs|Ms|Dr|St|Sr|Jr|vs|etc|e\.g|i\.e|cf|Capt|Lt|Col|Gen|"
        r"Rev|Prof|Sgt|Pvt|Cpl|Mt|Ft|Co|Ltd|Inc)\.",
        re.IGNORECASE,
    )
    masked = abbrev.sub(lambda m: m.group(0).replace(".", "§"), text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", masked)
    return [p.replace("§", ".").strip() for p in parts if p.strip()]


CHUNK_CHARS_DIALOGUE = 50  # force ~1-sentence Talos chunks so F5 inserts
                            # a natural breath between sentences instead of
                            # rapid-firing them in one breath.


def chunk_for_tts(text, max_chars=CHUNK_CHARS):
    """Chunk respecting paragraph breaks as hard boundaries — short opening
    lines like 'The Arimaspian Legacy' / 'by' / 'Gene Wolfe' should each be
    their own utterance, not packed into one rushed chunk."""
    out = []
    # Paragraph break = ≥1 blank line OR a line that's <40 chars and ends without
    # punctuation (likely a title fragment).
    # Short interjections (one or two words + punct) should NOT be packed
    # with neighbouring sentences — they need to be standalone chunks so
    # per-chunk pacing/ref-clip overrides only affect the interjection
    # itself, not the whole surrounding sentence.
    INTERJECTION_RE = re.compile(
        r"^(?:Hah|Ha|Lord|God|Yes|No|Oh|Ah|Bah|Right|Wrong|Liar)[.!?]?$",
        re.IGNORECASE,
    )
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for para in paragraphs:
        # Within a paragraph, sentence-pack as before
        buf = ""
        for sent in split_sentences(para):
            if INTERJECTION_RE.match(sent.strip()):
                # Flush buf, emit interjection as its own chunk
                if buf: out.append(buf); buf = ""
                out.append(sent.strip())
                continue
            if len(sent) > max_chars:
                # FLUSH the running sentence-pack buffer FIRST. Without
                # this, when a long sentence is comma-split, the parts
                # get appended before `buf`, reordering output relative
                # to source. (Symptom: sentences appearing out of order
                # in the rendered audio.)
                if buf:
                    out.append(buf)
                    buf = ""
                parts = re.split(r",\s+", sent)
                sub = ""
                for p in parts:
                    if not p:
                        continue
                    if len(sub) + len(p) + 2 <= max_chars:
                        sub = (sub + ", " + p).lstrip(", ")
                    else:
                        if sub: out.append(sub)
                        if len(p) > max_chars:
                            # Split on word boundaries, not raw character
                            # slices. The old `p[i:i+max_chars]` produced
                            # mid-word splits like "the griffins t|hat
                            # guard..." which F5 then synthed as a stutter.
                            words = p.split()
                            line = ""
                            for w in words:
                                if len(line) + len(w) + 1 <= max_chars:
                                    line = (line + " " + w).strip()
                                else:
                                    if line: out.append(line)
                                    line = w
                            if line: out.append(line)
                            sub = ""
                        else:
                            sub = p
                if sub:
                    # buf was already flushed at the start of this branch
                    out.append(sub)
                continue
            if len(buf) + len(sent) + 1 <= max_chars:
                buf = (buf + " " + sent).strip()
            else:
                if buf: out.append(buf)
                buf = sent
        if buf: out.append(buf)
    # Question-end emphasis was attempted via last-word chunk splitting
    # with a +1 semitone pitch lift. In practice F5 cannot reliably
    # synthesize single-word chunks (it produced "decisions" for
    # "visions?", "Terra" for "Patera?", etc.) — phonetically nearby
    # word substitution. Disabled. Keep the pass for future re-use.
    expanded = out
    # Parenthetical aside split: "blah (foo) bar" → ["blah,", PAREN+"foo,",
    # PAREN+"bar"]. The leading-paren marker on the in-paren and trailing
    # chunks asks the concat to insert a brief seam silence before them,
    # mimicking the audible "aside" pacing of a human reader.
    paren_expanded = []
    paren_re = re.compile(r"\(([^()]+)\)")
    for ch in expanded:
        # Strip any prefix markers; reattach to first emitted piece
        body, q_emph = apply_q_emphasis(ch)
        if "(" not in body or ")" not in body:
            paren_expanded.append(ch)
            continue
        last_end = 0
        pieces = []
        for m in paren_re.finditer(body):
            before = body[last_end:m.start()].rstrip(" ,")
            inner = m.group(1).strip(" ,")
            if before:
                pieces.append((before + ",", False))
            if inner:
                pieces.append((inner + ",", True))
            last_end = m.end()
        tail = body[last_end:].strip(" ,")
        if tail:
            pieces.append((tail, True))
        # Reattach Q_EMPHASIS to the last piece if it was on the original
        if q_emph and pieces:
            txt, pmark = pieces[-1]
            pieces[-1] = (Q_EMPHASIS_PREFIX + txt, pmark)
        for i, (txt, paren_mark) in enumerate(pieces):
            if paren_mark and i > 0:
                paren_expanded.append(PAREN_ASIDE_PREFIX + txt)
            elif paren_mark:
                paren_expanded.append(PAREN_ASIDE_PREFIX + txt)
            else:
                paren_expanded.append(txt)
    return paren_expanded


# Marker that prefixes a chunk to flag it for question-emphasis pitch
# shift. Stripped before display + TTS by `apply_q_emphasis()`.
Q_EMPHASIS_PREFIX = "​"  # zero-width space; survives JSON, removed pre-TTS
Q_EMPHASIS_SEMI = 1.0          # pitch lift in semitones for last-word emphasis

# Parenthetical-aside marker. Chunk so prefixed gets an extra silence
# inserted BEFORE it during concat (so the parenthetical and the prose
# after it are audibly set apart).
PAREN_ASIDE_PREFIX = "⁠"   # word joiner; distinct from Q_EMPHASIS_PREFIX
PAREN_ASIDE_SILENCE_SEC = 0.30  # short beat, less than voice-change (0.65)


def apply_q_emphasis(ch: str) -> tuple[str, bool]:
    """Return (clean_text, is_emphasized)."""
    if ch.startswith(Q_EMPHASIS_PREFIX):
        return ch[len(Q_EMPHASIS_PREFIX):], True
    return ch, False


def parse_chunk_markers(ch: str) -> tuple[str, dict]:
    """Strip all leading chunk markers and return (clean_text, flags)."""
    flags = {"q_emphasis": False, "paren_aside": False}
    changed = True
    while changed:
        changed = False
        if ch.startswith(Q_EMPHASIS_PREFIX):
            ch = ch[len(Q_EMPHASIS_PREFIX):]; flags["q_emphasis"] = True; changed = True
        if ch.startswith(PAREN_ASIDE_PREFIX):
            ch = ch[len(PAREN_ASIDE_PREFIX):]; flags["paren_aside"] = True; changed = True
    return ch, flags


def build_segments(book_text, attributions):
    """Walk book_text in order, emitting:
       [(start_char, end_char, kind, speaker, text), ...]
       where kind ∈ {'narration', 'dialogue'}.

    Attribution char_end values can overshoot the dialogue's actual closing
    quote by a few characters (eating part of the following narration like
    'I c' in '"Frequently," I confessed.'). To stay robust, we re-derive
    each span's real boundary by searching for the dialogue's own quoted
    text within the book starting at char_start. This guarantees the
    narration that follows starts at a real word boundary."""
    spans = sorted(attributions, key=lambda a: a["char_start"])
    out = []
    cursor = 0
    for a in spans:
        cs = a["char_start"]
        dlg = a.get("text", "").strip()
        if not dlg:
            cursor = a["char_end"]
            continue
        # Snap ce to the dialogue's true end. The attribution dialogue
        # text may contain raw newlines (from un-cleaned source) while
        # book_text has been through clean_text which collapses single
        # \n into spaces. So we use just the FIRST WORD of the dialogue
        # to locate the start, then take len(dlg) characters from there
        # (length is preserved by clean_text — \n→space is 1→1).
        first_word = dlg.split(None, 1)[0] if dlg.split() else ""
        # Try increasingly wide search windows.
        cs_real = -1
        for window in (50, 200, 1000):
            lo = max(0, cs - window); hi = min(len(book_text), cs + window)
            idx = book_text.find(first_word, lo, hi)
            if idx >= 0:
                cs_real = idx; break
        if cs_real < 0:
            cs_real = cs
        ce_real = cs_real + len(dlg)
        if cs_real > cursor:
            narr = book_text[cursor:cs_real].strip()
            if narr:
                # Narration excitement comes from F5 reading `!` with
                # raised pitch. Severian's narrator voice should stay
                # measured even when the author writes "Finnish knife!"
                # for emphasis. Soften descriptive exclamations to
                # periods, but only in narration (dialogue keeps its
                # rhetorical force).
                narr = narr.replace("!", ".")
                out.append((cursor, cs_real, "narration", "narrator", narr))
        # Source dialogue from cleaned book_text (not the raw attribution
        # text). This way: (a) typos fixed in cleaned.txt automatically
        # propagate to dialogue, (b) clean_text rewrites (-- → comma,
        # \n → space) apply to dialogue too. Result: F5 doesn't choke on
        # raw dashes in lines like "I'm -- hah! Potentially..."
        dlg_clean = book_text[cs_real:ce_real].strip()
        # Strip `!` from ALL dialogue — user wants zero excitability
        # across the cast. F5 reads `!` as pitch+volume lift, which
        # makes Talos's "Good, good!" sound boyish. The Hah/Lord
        # interjections are isolated chunks and rendered separately
        # via the take-substitution pipeline.
        dlg_clean = dlg_clean.replace("!", ".")
        out.append((cs_real, ce_real, "dialogue", a["speaker"], dlg_clean))
        cursor = ce_real
    if cursor < len(book_text):
        tail = book_text[cursor:].strip()
        if tail:
            tail = tail.replace("!", ".")
            out.append((cursor, len(book_text), "narration", "narrator", tail))
    return out


def clean_text(raw):
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    raw = re.sub(r"file://[^\s]*", " ", raw)
    raw = re.sub(r"[“”„]", '"', raw)
    raw = re.sub(r"[‘’‚]", "'", raw)
    raw = re.sub(r"\s*[—–−]\s*", ", ", raw)
    # ASCII double-hyphen used as em-dash in plaintext sources. F5 reads
    # "his -- there" as "his... T. here" because it pauses on the dashes
    # then mis-tokenizes the following word's first letter as a separate
    # token. Convert to a comma like the other dash variants.
    raw = re.sub(r"\s*--\s*", ", ", raw)
    # Hyphen-joined compounds confuse the F5 tokenizer (it emits a pause
    # at the hyphen and may slur the next word onto a following one).
    # Replace word-hyphen-word with a space so each becomes its own token.
    # Only fires between two letter words — preserves number ranges and
    # leading-hyphen contractions.
    raw = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", " ", raw)
    # Normalize "blank" lines that contain only whitespace into true \n\n
    # paragraph breaks. Then collapse REAL single mid-sentence newlines
    # into spaces. Order matters: without step 1, a " \n" sequence
    # (space + newline) is treated as a single newline and step 2 would
    # destroy paragraph structure.
    raw = re.sub(r"\n[ \t]+\n", "\n\n", raw)
    raw = re.sub(r"(?<!\n)\n(?!\n)", " ", raw)
    return raw


# ── Synth ─────────────────────────────────────────────────────────────

def load_f5(run, ckpt):
    import torch
    _orig = torch.load
    def _patched(*a, **kw):
        kw.setdefault("weights_only", False)
        return _orig(*a, **kw)
    torch.load = _patched
    from f5_tts.api import F5TTS
    ckpt_path = ROOT / f"venv/lib/python3.10/ckpts/{run}/{ckpt}.pt"
    vocab_path = ROOT / f"venv/lib/python3.10/data/{run}_pinyin/vocab.txt"
    return F5TTS(model="F5TTS_v1_Base",
                 ckpt_file=str(ckpt_path), vocab_file=str(vocab_path),
                 device="cuda")


def synth_chunk(f5, ref_audio, ref_text, gen_text, dst_raw, dst_final,
                speed=0.7, trim_tail_ms=0, pitch_shift_semitones=0,
                seed=None):
    # Seeded sampling makes a given (chunk text, ref, voice) deterministic
    # across reruns — without it, F5 rolls fresh diffusion noise every
    # call and quality varies. We pass seq_idx as the seed from the caller.
    f5.infer(ref_file=str(ref_audio), ref_text=ref_text, gen_text=gen_text,
             file_wave=str(dst_raw), show_info=lambda *a, **k: None,
             remove_silence=True, speed=speed, seed=seed,
             # cfg_strength=1.8 (was 2.0) — main fix for repeated-word
             # loops; flow-matching TTS over-commits to a phoneme
             # attractor at cfg≥2.0. nfe_step=48 plateaus quality with
             # ~25% less compute than 64. target_rms=0.15 stabilises
             # ref-loudness conditioning across heterogeneous refs.
             cfg_strength=1.8, nfe_step=48, target_rms=0.15)
    # Strip leading silence/garbled-onset (F5 sometimes prepends noise or
    # half-words to the first ~100-300ms). Use silenceremove to drop any
    # leading audio below -45 dB before the first real syllable.
    af = ("silenceremove=start_periods=1:start_duration=0.05:"
          "start_threshold=-45dB,"
          + EQ)
    if trim_tail_ms > 0:
        # apad/areverse trick: reverse, drop leading silence/short audio, reverse back.
        # Simpler: use atrim end at (duration - trim_tail_ms/1000).
        af = (f"areverse,atrim=start={trim_tail_ms/1000.0},asetpts=PTS-STARTPTS,"
              f"areverse,{EQ}")
    # Optional pitch shift (used by interjections to sound prouder/more
    # affluent). +N semitones via asetrate (raises pitch + duration),
    # then atempo to undo the duration change. 12 semitones = 2x rate.
    if pitch_shift_semitones != 0:
        ratio = 2 ** (pitch_shift_semitones / 12.0)
        # Use 24kHz as our base sample rate since F5 outputs 24k.
        pitch_filter = (f"asetrate=24000*{ratio:.5f},"
                        f"aresample=24000,atempo=1/{ratio:.5f}")
        af = pitch_filter + "," + af
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(dst_raw),
                    "-af", af, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s16le",
                    str(dst_final)], check=True)
    dst_raw.unlink()


# Extra silence inserted between consecutive chunks whose voice differs
# (e.g. narrator → silk). Since this is one reader doing all the voices,
# the pause mimics the cognitive cost of switching characters. 650ms ≈
# what a human audiobook narrator takes between dialogue turns.
VOICE_CHANGE_SILENCE_SEC = 0.65


def _voice_from_filename(p):
    # chunk_NNNN_<voice>.wav  →  <voice>
    stem = p.stem  # e.g. chunk_0042_severian-c
    parts = stem.split("_", 2)
    return parts[2] if len(parts) >= 3 else ""


def ffmpeg_concat(inputs, out):
    """Concat the chunks in order, inserting extra silence at voice-change
    seams AND at paren-aside seams (read from plan.jsonl if present).
    Then apply ONE master dynamics pass."""
    # Per-chunk extras (paren-aside). seq → silence_before_sec.
    plan_path = out.parent / "plan.jsonl"
    extras = {}
    if plan_path.exists():
        for line in plan_path.read_text().splitlines():
            if not line.strip(): continue
            r = json.loads(line)
            es = float(r.get("extra_silence_before", 0) or 0)
            if es > 0:
                extras[r["seq"]] = es

    def seq_of(p):
        # chunk_NNNN_<voice>.wav → NNNN
        try: return int(p.stem.split("_")[1])
        except Exception: return None

    silence_cache: dict[float, Path] = {}
    def silence_wav(sec: float) -> Path:
        sec = round(sec, 3)
        if sec not in silence_cache:
            p = out.parent / f"_silence_{int(sec*1000)}ms.wav"
            if not p.exists():
                subprocess.run(
                    ["ffmpeg", "-y", "-loglevel", "error",
                     "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
                     "-t", str(sec),
                     "-c:a", "pcm_s16le", str(p)], check=True)
            silence_cache[sec] = p
        return silence_cache[sec]

    list_file = out.with_suffix(".concat.txt")
    with list_file.open("w") as f:
        prev_voice = None
        for p in inputs:
            v = _voice_from_filename(p)
            seq = seq_of(p)
            sil_sec = 0.0
            if prev_voice is not None and v != prev_voice:
                sil_sec = max(sil_sec, VOICE_CHANGE_SILENCE_SEC)
            if seq is not None and seq in extras:
                sil_sec = max(sil_sec, extras[seq])
            if sil_sec > 0:
                f.write(f"file '{silence_wav(sil_sec).resolve()}'\n")
            f.write(f"file '{p.resolve()}'\n")
            prev_voice = v
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", str(list_file),
                    "-af", MASTER_DYNAMICS,
                    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "1", str(out)],
                   check=True)
    list_file.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument("attribution")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pacing", default=None,
                    help="optional pacing.jsonl from preprocess_for_tts.py")
    ap.add_argument("--no-play", action="store_true")
    args = ap.parse_args()

    # Optional pacing overrides: char_start/end → {speed, trim_tail_ms}
    pacing_marks = []
    if args.pacing and Path(args.pacing).exists():
        for line in Path(args.pacing).read_text().splitlines():
            if line.strip():
                pacing_marks.append(json.loads(line))
        print(f"loaded {len(pacing_marks)} pacing marks")

    book = Path(args.book).read_text(encoding="utf-8", errors="replace")
    book = clean_text(book)
    attrs = [json.loads(l) for l in Path(args.attribution).read_text().splitlines() if l.strip()]
    segments = build_segments(book, attrs)
    print(f"segments: {len(segments)} "
          f"(narration={sum(1 for s in segments if s[2]=='narration')}, "
          f"dialogue={sum(1 for s in segments if s[2]=='dialogue')})")

    # Helper — does this chunk text match an interjection pacing mark?
    def chunk_pacing(chunk_text: str) -> dict:
        """If chunk text overlaps a pacing mark (by content match), return
        its override dict. Defaults to speed=0.7 / no tail trim."""
        for pm in pacing_marks:
            # Tolerant match: pacing text might be "Hah." while chunk
            # contains "Hah!" (Talos's dialogue retains its !). Normalize
            # both sides — strip end punctuation before comparing.
            import re as _re
            pm_norm = _re.sub(r"[.!?]+\s*$", "", pm["text"].strip().strip('"'))
            ch_norm = _re.sub(r"[.!?]", "", chunk_text)
            if pm_norm in ch_norm or pm["text"].strip().strip('"') in chunk_text:
                return pm
        return {"speed": 0.7, "trim_tail_ms": 0}

    # Resolve voice + ref per segment, chunk, queue per voice in order.
    plan = []  # [(seq_idx, voice_label, run, ckpt, ref_path, ref_text, gen_text, pacing)]
    seq = 0
    for cs, ce, kind, speaker, text in segments:
        if kind == "narration" or speaker not in SPEAKER_TO_VOICE:
            voice_label, run, ckpt, char_label = NARRATOR_VOICE
        else:
            voice_label, run, ckpt, char_label = SPEAKER_TO_VOICE[speaker]
        if char_label:
            ref_path, ref_text = char_ref(char_label)
            if not ref_path:
                ref_path, ref_text = narrator_ref()
        else:
            ref_path, ref_text = narrator_ref()
        # Dialogue uses smaller chunks → mostly one sentence per chunk
        # → F5 puts a real breath between Talos's sentences instead of
        # smashing "Hah. In glass, too. No plastic. Not for us, crystal."
        # into one rapid breath.
        chunk_size = CHUNK_CHARS_DIALOGUE if kind == "dialogue" else CHUNK_CHARS
        speaker_pitch = SPEAKER_PITCH.get(speaker, 0.0)
        for ch in chunk_for_tts(text, max_chars=chunk_size):
            ch, flags = parse_chunk_markers(ch)
            pm = dict(chunk_pacing(ch))
            base_pitch = pm.get("pitch_shift_semitones", speaker_pitch)
            if flags["q_emphasis"]:
                base_pitch = base_pitch + Q_EMPHASIS_SEMI
            if base_pitch:
                pm["pitch_shift_semitones"] = base_pitch
            if flags["paren_aside"]:
                pm["extra_silence_before"] = PAREN_ASIDE_SILENCE_SEC
            tts_ch = apply_tts_overrides(ch)
            # 9-tuple: (seq, voice, run, ckpt, ref_path, ref_text,
            #          display_text, tts_text, pacing)
            plan.append((seq, voice_label, run, ckpt, ref_path, ref_text,
                         ch, tts_ch, pm))
            seq += 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = out_dir / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    raw_dir = out_dir / "raw"
    # QC retry plumbing: if a previous run wrote seed_overrides.json
    # (via scripts/qc_chunks.py --delete-bad), use those seeds for the
    # listed sequences. Re-renders use a fresh seed → different dice
    # roll than the failed attempt.
    seed_override_path = out_dir / "seed_overrides.json"
    seed_overrides: dict[int, int] = {}
    if seed_override_path.exists():
        try:
            seed_overrides = {int(k): int(v)
                              for k, v in json.loads(seed_override_path.read_text()).items()}
            print(f"loaded {len(seed_overrides)} seed overrides from prior QC pass")
        except Exception as e:
            print(f"seed_overrides.json unreadable: {e}", file=sys.stderr)
    raw_dir.mkdir(exist_ok=True)

    # Save the plan for reference. Both `text` (display, what subtitles
    # show) and `tts_text` (what F5 actually synthesized — may differ for
    # heteronym overrides like "lives" → "livz") are recorded.
    with (out_dir / "plan.jsonl").open("w") as f:
        for seq_idx, vl, run, ckpt, rp, rt, gt, tts_gt, pm in plan:
            f.write(json.dumps({
                "seq": seq_idx, "voice": vl, "run": run, "ckpt": ckpt,
                "text": gt, "tts_text": tts_gt,
                "speed": pm.get("speed", 0.7),
                "trim_tail_ms": pm.get("trim_tail_ms", 0),
                "pitch_shift_semitones": pm.get("pitch_shift_semitones", 0),
                "extra_silence_before": pm.get("extra_silence_before", 0),
            }) + "\n")

    # Group consecutive chunks by voice to minimize model loads
    by_voice = {}
    for entry in plan:
        seq_idx, vl, run, ckpt, rp, rt, gt, tts_gt, pm = entry
        by_voice.setdefault((vl, run, ckpt), []).append(entry)
    print(f"total chunks: {len(plan)}; voice groups: {len(by_voice)}")
    print(f"voices: {sorted({k[0] for k in by_voice})}")
    n_fast = sum(1 for _, _, _, _, _, _, _, _, pm in plan if pm.get("speed", 0.7) != 0.7)
    if n_fast:
        print(f"  {n_fast} chunks tagged with custom pacing")

    # Player thread plays files in seq order as they become ready
    ready = {}  # seq_idx -> path
    ready_lock = threading.Lock()
    next_to_play = 0
    play_done_event = threading.Event()

    def player():
        nonlocal next_to_play
        while next_to_play < len(plan):
            time.sleep(0.5)
            while True:
                with ready_lock:
                    p = ready.get(next_to_play)
                if not p:
                    break
                if not args.no_play:
                    subprocess.run(["paplay", str(p)],
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                with ready_lock:
                    next_to_play += 1
        play_done_event.set()

    player_t = threading.Thread(target=player, daemon=True)
    player_t.start()

    # Synth voice by voice (each loaded once). Skip any voice group whose
    # chunks are ALL already on disk (lets a crashed run resume without
    # reloading the model or re-synthesizing finished chunks).
    for (vl, run, ckpt), entries in by_voice.items():
        pending = [e for e in entries
                   if not (chunks_dir / f"chunk_{e[0]:04d}_{vl}.wav").exists()]
        for e in entries:
            done = chunks_dir / f"chunk_{e[0]:04d}_{vl}.wav"
            if done.exists():
                with ready_lock:
                    ready[e[0]] = done
        if not pending:
            print(f"\n=== {vl}: all {len(entries)} chunks cached, skipping ===", flush=True)
            continue
        print(f"\n=== loading {vl} ({run}/{ckpt}) for {len(pending)}/{len(entries)} chunks ===", flush=True)
        f5 = load_f5(run, ckpt)
        # Rolling-reference stitching attempted to mimic ElevenLabs'
        # previous_request_ids; in practice F5 treated the extended
        # ref_text as content-to-speak and produced wholesale repeats
        # of prior chunk content. Disabled (ANCHOR_EVERY=1 → every
        # chunk re-anchors to canonical ref). Keeping the scaffolding
        # so the QC retry loop can still tweak refs per attempt.
        ANCHOR_EVERY = 1
        prev_chunk_for_voice: dict[str, tuple[Path, str]] = {}
        stitch_counter: dict[str, int] = {}
        stitch_tmp_dir = out_dir / "_stitch_refs"
        stitch_tmp_dir.mkdir(exist_ok=True)
        for seq_idx, _, _, _, ref_path, ref_text, display_text, tts_text, pm in pending:
            dst_raw = raw_dir / f"raw_{seq_idx:04d}.wav"
            dst_final = chunks_dir / f"chunk_{seq_idx:04d}_{vl}.wav"
            speed = float(pm.get("speed", 0.7))
            trim_tail_ms = int(pm.get("trim_tail_ms", 0))
            pitch_semitones = float(pm.get("pitch_shift_semitones", 0))
            # Per-pacing-mark reference clip override (interjections).
            chunk_ref = ref_path
            chunk_ref_text = ref_text
            if pm.get("ref_clip"):
                chunk_ref = Path(pm["ref_clip"])
                chunk_ref_text = pm.get("ref_text", ref_text)

            # Build rolling reference: canonical_ref + previous_chunk_audio.
            # Mimics ElevenLabs request-stitching so F5 sees actual prior
            # output as conditioning. Skip on interjection refs (single
            # affect) and on every Nth chunk (anchor reset).
            cnt = stitch_counter.get(vl, 0)
            prev = prev_chunk_for_voice.get(vl)
            do_stitch = (prev is not None
                         and not pm.get("ref_clip")
                         and cnt % ANCHOR_EVERY != 0)
            stitch_wav = None
            if do_stitch:
                stitch_wav = stitch_tmp_dir / f"stitch_{vl}_{seq_idx:04d}.wav"
                concat_txt = stitch_wav.with_suffix(".txt")
                with concat_txt.open("w") as f:
                    f.write(f"file '{chunk_ref.resolve()}'\n")
                    f.write(f"file '{prev[0].resolve()}'\n")
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-loglevel", "error",
                         "-f", "concat", "-safe", "0", "-i", str(concat_txt),
                         "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                         str(stitch_wav)], check=True)
                    chunk_ref = stitch_wav
                    chunk_ref_text = chunk_ref_text + " " + prev[1]
                except Exception as e:
                    print(f"  stitch failed @ {seq_idx}: {e}", file=sys.stderr)
                    do_stitch = False
                finally:
                    concat_txt.unlink(missing_ok=True)

            try:
                synth_chunk(f5, chunk_ref, chunk_ref_text, tts_text,
                            dst_raw, dst_final,
                            speed=speed, trim_tail_ms=trim_tail_ms,
                            pitch_shift_semitones=pitch_semitones,
                            seed=seed_overrides.get(seq_idx, seq_idx))
                with ready_lock:
                    ready[seq_idx] = dst_final
                # Update rolling state with this chunk
                prev_chunk_for_voice[vl] = (dst_final, display_text)
                stitch_counter[vl] = cnt + 1
                marker = f" speed={speed}" if speed != 0.7 else ""
                anchor_mark = "" if do_stitch else " ⚓"
                print(f"  ✓ {seq_idx:>3d} [{vl}{marker}{anchor_mark}] {display_text[:55]!r}", flush=True)
            except Exception as e:
                print(f"  ✗ {seq_idx:>3d} {e}", file=sys.stderr, flush=True)
            finally:
                if stitch_wav is not None and stitch_wav.exists():
                    stitch_wav.unlink()
        del f5
        import torch
        gc.collect(); torch.cuda.empty_cache()

    # Wait for playback to drain
    print("\nwaiting for playback to finish...")
    play_done_event.wait()

    # Master concat
    ordered = [ready[i] for i in range(len(plan)) if i in ready]
    if len(ordered) > 1:
        master = out_dir / "complete.wav"
        ffmpeg_concat(ordered, master)
        sz = master.stat().st_size / 1024 / 1024
        print(f"\nmaster: {master} ({sz:.1f} MB)")

    print("\ndone")


if __name__ == "__main__":
    sys.exit(main())
