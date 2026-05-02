#!/usr/bin/env python3
"""LCARS-styled clip curator for the Majel fine-tune dataset.

Walks `dataset/clips_ranked/*.wav` (sorted by ECAPA cosine to a Majel ref),
plays each clip on demand, lets the human accept / reject / trim with
keyboard shortcuts, and emits two manifests:

  dataset/manifest_curated.jsonl   one JSON per accepted clip
                                   {clip_id, src, start, end, transcript}
  dataset/manifest_rejected.jsonl  one JSON per rejected clip

Accepted clips are also copied to `dataset/clips_curated/` (or trimmed and
re-encoded with ffmpeg if the trim window was adjusted), so the F5-TTS /
GPT-SoVITS fine-tuner can point at one folder.

Resume: the GUI reads both manifests on launch and skips already-decided
clip IDs, so you can quit and resume mid-session.

Keyboard:
  Space  play / replay
  1      accept clip with current trim window
  0      reject clip
  J / →  next clip
  K / ←  previous clip
  T      cycle the trim mode (full / left-trim / right-trim / both)
  R      reset trim to full clip
  Q      quit
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

import numpy as np
import soundfile as sf
from spellchecker import SpellChecker

# Trek-specific proper nouns / made-up vocabulary so the spellchecker
# doesn't flag every "Klingon" or "Picard" in the transcripts.
TREK_VOCAB = (
    "starfleet klingon romulan vulcan borg ferengi bajoran cardassian "
    "tholian breen suliban xindi ocampa kazon talaxian denobulan andorian "
    "picard spock worf riker geordi troi bashir kira odo dax sisko quark "
    "janeway chakotay tuvok neelix paris kim seven archer tucker phlox "
    "majel roddenberry barrett trek "
    "phaser holodeck lcars subspace dilithium tricorder deflector warp "
    "nacelle turbolist isolinear bioneural combadge starbase shuttlecraft "
    "shuttlebay sickbay jefferies impulse photon nebula plasma replicator "
    "duotronic positronic transwarp tachyon "
).split()

ROOT = Path(__file__).resolve().parent.parent
RANKED_DIR = ROOT / "dataset" / "clips_ranked"
CURATED_DIR = ROOT / "dataset" / "clips_curated"
MANIFEST_RANKED = ROOT / "storage" / "majel_cosine.jsonl"
MANIFEST_ACCEPT = ROOT / "dataset" / "manifest_curated.jsonl"
MANIFEST_REJECT = ROOT / "dataset" / "manifest_rejected.jsonl"
# Per-clip transcript overrides typed by the user. Persisted across
# sessions so edits aren't lost when navigating away before deciding.
TRANSCRIPT_OVERRIDES = ROOT / "dataset" / "transcript_overrides.json"
# Per-clip quality flags. Used for samples that pass but have concerns
# (light music, mild noise, slight reverb, etc.) so the fine-tune step
# can drop or reweight them later.
FLAG_OVERRIDES = ROOT / "dataset" / "flag_overrides.json"

# LCARS palette (match majel_gui.py).
LCARS = {
    "bg":             "#000000",
    "violet":         "#ccaaff",
    "orange":         "#ff8800",
    "butterscotch":   "#ff9966",
    "sunflower":      "#ffcc99",
    "red":            "#cc4444",
    "bluey":          "#8899ff",
    "lima":           "#cccc66",
    "space_white":    "#f5f6fa",
}

WIN_W = 980
WIN_H = 740
TIPS_H = 170    # height of the tips block above the footer


def lcars_font(size: int = 12, weight: str = "bold") -> tkfont.Font:
    candidates = ("Antonio", "DejaVu Sans Condensed", "Liberation Sans Narrow",
                  "Helvetica")
    avail = set(tkfont.families())
    for n in candidates:
        if n in avail:
            return tkfont.Font(family=n, size=size, weight=weight)
    return tkfont.Font(size=size, weight=weight)


def parse_filename(p: Path) -> tuple[int, float, float, str]:
    """Filename format: NNN__C.CCC__D.Ds__transcript_slug.wav"""
    m = re.match(r"^(\d+)__([\d.]+)__([\d.]+)s__(.+)\.wav$", p.name)
    if not m:
        return 0, 0.0, 0.0, p.stem
    return int(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(4).replace("_", " ")


def load_decided() -> tuple[set[str], set[str]]:
    accepted, rejected = set(), set()
    if MANIFEST_ACCEPT.exists():
        for line in MANIFEST_ACCEPT.read_text().splitlines():
            try:
                accepted.add(json.loads(line)["clip_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    if MANIFEST_REJECT.exists():
        for line in MANIFEST_REJECT.read_text().splitlines():
            try:
                rejected.add(json.loads(line)["clip_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return accepted, rejected


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def load_transcript_overrides() -> dict[str, str]:
    if TRANSCRIPT_OVERRIDES.exists():
        try:
            return json.loads(TRANSCRIPT_OVERRIDES.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_transcript_overrides(d: dict[str, str]) -> None:
    TRANSCRIPT_OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRANSCRIPT_OVERRIDES.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True))
    tmp.replace(TRANSCRIPT_OVERRIDES)


def load_flag_overrides() -> dict[str, dict]:
    if FLAG_OVERRIDES.exists():
        try:
            data = json.loads(FLAG_OVERRIDES.read_text())
            # Normalise legacy bool entries to {flagged, note} dicts.
            return {k: (v if isinstance(v, dict)
                        else {"flagged": bool(v), "note": ""})
                    for k, v in data.items()}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_flag_overrides(d: dict[str, dict]) -> None:
    FLAG_OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    tmp = FLAG_OVERRIDES.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True))
    tmp.replace(FLAG_OVERRIDES)


def total_duration(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0.0
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
            total += float(row.get("duration", 0))
        except (json.JSONDecodeError, ValueError):
            continue
    return total


class Curator:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("LCARS · MAJEL CLIP CURATOR")
        root.geometry(f"{WIN_W}x{WIN_H}")
        root.configure(bg=LCARS["bg"])
        root.minsize(800, 480)

        self.f_title = lcars_font(20, "bold")
        self.f_section = lcars_font(13, "bold")
        self.f_label = lcars_font(11, "bold")
        self.f_mono = tkfont.Font(family="DejaVu Sans Mono", size=10)

        self.canvas = tk.Canvas(root, bg=LCARS["bg"],
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)

        # Build clip list, sorted by rank prefix in filename.
        self.clips: list[Path] = sorted(RANKED_DIR.glob("*.wav"),
                                        key=lambda p: parse_filename(p)[0])
        self.accepted, self.rejected = load_decided()
        self.transcript_overrides: dict[str, str] = load_transcript_overrides()
        self.flag_overrides: dict[str, dict] = load_flag_overrides()
        self.idx = self._first_undecided()

        # Editable transcript Entry, placed via canvas.create_window during render.
        self.transcript_var = tk.StringVar()
        self.transcript_entry = tk.Entry(
            self.canvas, textvariable=self.transcript_var,
            bg="#1a1a26", fg=LCARS["space_white"],
            insertbackground=LCARS["sunflower"],
            relief="flat", bd=0, font=self.f_label,
            highlightthickness=2, highlightbackground=LCARS["violet"],
            highlightcolor=LCARS["orange"],
        )
        # Pressing Return commits and returns focus to the canvas so
        # the keyboard shortcuts work again.
        self.transcript_entry.bind("<Return>", lambda e: self._commit_transcript_focus())
        self.transcript_entry.bind("<KP_Enter>", lambda e: self._commit_transcript_focus())
        # Stop the typing-autoplay loop the moment focus leaves the field.
        self.transcript_entry.bind("<FocusOut>", lambda e: self._stop_autoplay())
        # Persist on every keystroke change so edits survive a quit.
        self._transcript_save_after: str | None = None
        # When True, _on_transcript_changed is a no-op — set during the
        # programmatic var.set() in _render so loading a new clip doesn't
        # spuriously trigger autoplay or persist a fake "override".
        self._suppress_change_handler: bool = False
        self.transcript_var.trace_add("write", self._on_transcript_changed)

        # Spell check (pure-python, ~250k word dict). Trek vocab loaded so
        # proper nouns don't get flagged.
        self._spell = SpellChecker()
        self._spell.word_frequency.load_words(TREK_VOCAB)
        self._spell_msg: str = ""
        self._spell_after: str | None = None

        # While the user is typing in the transcript field, replay the
        # current clip on a loop so they can hear what they're transcribing.
        self._autoplay: bool = False
        # trim_l / trim_r are in seconds, relative to clip start.
        self.trim_l: float = 0.0
        self.trim_r: float = 0.0   # 0 means "use clip end"
        self.audio_data: np.ndarray | None = None
        self.audio_sr: int = 0
        self._play_proc: subprocess.Popen | None = None
        # Playhead animation state.
        self._play_start_t: float = 0.0
        self._play_dur: float = 0.0
        self._play_offset: float = 0.0   # seconds from start of full clip
        self._playhead_id: int | None = None
        self._tick_after: str | None = None

        # Bind shortcuts globally — but skip them while the transcript entry
        # is focused so typing works normally.
        def gate(handler):
            def wrapped(e):
                if root.focus_get() is self.transcript_entry:
                    return None
                return handler()
            return wrapped
        root.bind("<space>", gate(self._play))
        root.bind("1", gate(self._accept))
        root.bind("2", gate(self._accept_flagged))
        root.bind("0", gate(self._reject))
        root.bind("f", gate(self._toggle_flag))
        root.bind("j", gate(self._next))
        root.bind("<Right>", gate(self._next))
        root.bind("k", gate(self._prev))
        root.bind("<Left>", gate(self._prev))
        root.bind("r", gate(self._reset_trim))
        root.bind("q", gate(lambda: root.destroy()))

        # Mouse drag on the waveform sets the trim window.
        self.canvas.bind("<ButtonPress-1>", self._wave_press)
        self.canvas.bind("<B1-Motion>", self._wave_drag)
        self.canvas.bind("<ButtonRelease-1>", self._wave_release)
        self._drag_active = False
        self._drag_marker: str | None = None  # "L" or "R"

        # Transient banner (e.g. "TRIMMING…", "✓ ACCEPTED · …") shown on top
        # of the waveform after a decision so the user gets visible
        # confirmation that the trim + transcript actually persisted.
        self._flash_msg: str | None = None
        self._flash_color: str = LCARS["lima"]
        self._flash_after: str | None = None

        self._render()

    def _first_undecided(self) -> int:
        for i, p in enumerate(self.clips):
            if p.name not in self.accepted and p.name not in self.rejected:
                return i
        return 0

    def _on_resize(self, _ev):
        self._render()

    def _commit_transcript_focus(self):
        """User pressed Enter in the transcript field — save and refocus."""
        # The trace_add already saved; just hand focus back so shortcuts work.
        self.canvas.focus_set()

    def _on_transcript_changed(self, *_args):
        """Debounced persist of the transcript override on each edit."""
        if self._suppress_change_handler:
            return
        if self.idx >= len(self.clips):
            return
        clip_id = self.clips[self.idx].name
        new_text = self.transcript_var.get()
        if new_text:
            self.transcript_overrides[clip_id] = new_text
        else:
            self.transcript_overrides.pop(clip_id, None)
        if self._transcript_save_after:
            self.root.after_cancel(self._transcript_save_after)
        self._transcript_save_after = self.root.after(
            400, lambda: save_transcript_overrides(self.transcript_overrides))

        # Debounced spell check — display result line below the entry.
        if self._spell_after:
            self.root.after_cancel(self._spell_after)
        self._spell_after = self.root.after(250, self._update_spell)

        # Autoplay-on-loop while user is typing in the transcript field
        # (only when the entry is the focused widget — we don't want to
        # auto-play on programmatic var.set() during navigation).
        if self.root.focus_get() is self.transcript_entry:
            self._autoplay = True
            if self._play_proc is None or self._play_proc.poll() is not None:
                self._play()

    def _update_spell(self):
        self._spell_after = None
        text = self.transcript_var.get()
        words = re.findall(r"[A-Za-z']+", text)
        if not words:
            self._spell_msg = ""
            self._render()
            return
        misspelled = self._spell.unknown(w.lower() for w in words)
        if not misspelled:
            self._spell_msg = "✓ SPELL OK"
            self._render()
            return
        seen: set[str] = set()
        suggestions: list[str] = []
        for w in words:
            wl = w.lower()
            if wl in misspelled and wl not in seen:
                seen.add(wl)
                sug = self._spell.correction(wl) or "?"
                suggestions.append(f"'{w}' → '{sug}'")
                if len(suggestions) >= 3:
                    break
        self._spell_msg = "✗ " + "   ".join(suggestions)
        self._render()

    def _stop_autoplay(self):
        """Called when transcript field loses focus — kill the loop but
        let the currently-playing audio finish naturally so we don't cut
        off mid-word every time the user shifts focus."""
        self._autoplay = False

    def _current_transcript(self) -> str:
        if self.idx >= len(self.clips):
            return ""
        clip_id = self.clips[self.idx].name
        if clip_id in self.transcript_overrides:
            return self.transcript_overrides[clip_id]
        return parse_filename(self.clips[self.idx])[3]

    def _is_flagged(self, clip_id: str) -> bool:
        return bool(self.flag_overrides.get(clip_id, {}).get("flagged"))

    def _toggle_flag(self):
        if self.idx >= len(self.clips):
            return
        clip_id = self.clips[self.idx].name
        cur = self.flag_overrides.get(clip_id, {"flagged": False, "note": ""})
        cur["flagged"] = not cur["flagged"]
        if not cur["flagged"]:
            cur["note"] = ""
        self.flag_overrides[clip_id] = cur
        save_flag_overrides(self.flag_overrides)
        self._render()

    def _accept_flagged(self):
        """Accept the current clip and mark it flagged in one keystroke."""
        if self.idx >= len(self.clips):
            return
        clip_id = self.clips[self.idx].name
        cur = self.flag_overrides.get(clip_id, {"flagged": False, "note": ""})
        cur["flagged"] = True
        self.flag_overrides[clip_id] = cur
        save_flag_overrides(self.flag_overrides)
        self._accept()

    # ── Render ────────────────────────────────────────────────────────────
    def _render(self):
        c = self.canvas
        c.delete("all")
        W = c.winfo_width() or WIN_W
        H = c.winfo_height() or WIN_H

        # Header bar
        c.create_rectangle(0, 0, W, 40, fill=LCARS["bluey"], outline=LCARS["bluey"])
        c.create_text(20, 20, anchor="w", text="MAJEL CLIP CURATOR",
                      fill=LCARS["bg"], font=self.f_title)
        # Counters
        n_total = len(self.clips)
        n_acc = len(self.accepted)
        n_rej = len(self.rejected)
        n_left = n_total - n_acc - n_rej
        n_flag = sum(1 for cid in self.accepted
                     if self._is_flagged(cid))
        acc_dur = total_duration(MANIFEST_ACCEPT)
        c.create_text(W - 20, 20, anchor="e",
                      text=f"ACCEPTED {n_acc} (FLAGGED {n_flag})  REJECTED {n_rej}  REMAINING {n_left}  · {acc_dur/60:.1f} MIN",
                      fill=LCARS["bg"], font=self.f_label)

        # Footer bar with hotkeys
        c.create_rectangle(0, H - 40, W, H, fill=LCARS["orange"], outline=LCARS["orange"])
        c.create_text(W / 2, H - 20, anchor="center",
                      text="SPACE play   1 accept   2 accept+flag   0 reject   F flag   J/K next/prev   R reset trim   Q quit",
                      fill=LCARS["bg"], font=self.f_label)

        if not self.clips:
            c.create_text(W/2, H/2, text="NO CLIPS IN dataset/clips_ranked/",
                          fill=LCARS["sunflower"], font=self.f_section)
            return

        if self.idx >= len(self.clips):
            c.create_text(W/2, H/2, text="ALL CLIPS DECIDED · QUIT WITH Q",
                          fill=LCARS["sunflower"], font=self.f_title)
            return

        p = self.clips[self.idx]
        rank, cos, dur, transcript = parse_filename(p)

        # Status badges
        y = 60
        decision = "PENDING"
        decision_color = LCARS["sunflower"]
        if p.name in self.accepted:
            decision, decision_color = "ACCEPTED", LCARS["lima"]
        elif p.name in self.rejected:
            decision, decision_color = "REJECTED", LCARS["red"]
        flagged_now = self._is_flagged(p.name)

        # Top metadata block
        c.create_text(20, y, anchor="nw",
                      text=f"CLIP {self.idx+1} / {len(self.clips)}",
                      fill=LCARS["sunflower"], font=self.f_section)
        # Right side: optional FLAGGED badge + decision badge
        right_x = W - 20
        c.create_text(right_x, y, anchor="ne", text=decision,
                      fill=decision_color, font=self.f_section)
        if flagged_now:
            badge_x = right_x - 200
            c.create_text(badge_x, y, anchor="ne", text="⚑ FLAGGED",
                          fill=LCARS["butterscotch"], font=self.f_section)
        y += 26
        c.create_text(20, y, anchor="nw",
                      text=f"COSINE {cos:.3f}   DURATION {dur:.1f}s   RANK {rank}",
                      fill=LCARS["violet"], font=self.f_label)
        y += 22
        c.create_text(20, y, anchor="nw",
                      text=f"FILE  {p.name}",
                      fill=LCARS["space_white"], font=self.f_mono)
        y += 22

        # Editable transcript field.
        c.create_text(20, y, anchor="nw", text="TRANSCRIPT",
                      fill=LCARS["sunflower"], font=self.f_label)
        # Sync the entry value to the current clip's transcript (override
        # if present, otherwise the parsed-slug default). Skip if the entry
        # already has the right value to avoid stomping in-progress typing.
        target = self._current_transcript()
        if self.transcript_var.get() != target:
            # Disable trace temporarily so this assignment doesn't trigger
            # an "override save" / autoplay cycle.
            self._suppress_change_handler = True
            try:
                self.transcript_var.set(target)
            finally:
                self._suppress_change_handler = False
        # Place the entry just to the right of the label so it spans most
        # of the available width.
        entry_x = 130
        entry_w = max(200, W - entry_x - 24)
        # Estimated entry height = font line height + small padding.
        c.create_window(entry_x, y - 2, anchor="nw", window=self.transcript_entry,
                        width=entry_w, height=26)
        y += 28
        # Spell-check status line — kept on the same row as the field
        # right edge to avoid eating waveform vertical space.
        if self._spell_msg:
            ok = self._spell_msg.startswith("✓")
            c.create_text(entry_x, y, anchor="nw", text=self._spell_msg,
                          fill=(LCARS["lima"] if ok else LCARS["butterscotch"]),
                          font=self.f_mono)
        y += 18

        # Waveform area — shrinks to leave room for the tips block + footer.
        wave_top = y + 4
        # Reserve TIPS_H for the tips block, plus 40 for footer, plus 60 for
        # the trim-readout line below the waveform.
        wave_h = H - 40 - TIPS_H - wave_top - 60
        wave_x0, wave_x1 = 20, W - 20
        c.create_rectangle(wave_x0, wave_top, wave_x1, wave_top + wave_h,
                           fill="#0c0c1a", outline=LCARS["violet"], width=2)
        self._wave_box = (wave_x0, wave_top, wave_x1, wave_top + wave_h)

        # Load audio if needed
        if self.audio_data is None:
            try:
                self.audio_data, self.audio_sr = sf.read(str(p), dtype="float32")
                if self.audio_data.ndim > 1:
                    self.audio_data = self.audio_data.mean(axis=1)
            except Exception:
                self.audio_data = np.zeros(1, dtype=np.float32)
                self.audio_sr = 22050

        # Draw waveform
        n = len(self.audio_data)
        if n > 0:
            buckets = wave_x1 - wave_x0 - 4
            if buckets > 0:
                step = max(1, n // buckets)
                mid_y = wave_top + wave_h / 2
                amp = wave_h * 0.45
                for i in range(buckets):
                    s = i * step
                    e = min(n, s + step)
                    chunk = self.audio_data[s:e]
                    if len(chunk) == 0:
                        continue
                    peak = float(np.max(np.abs(chunk))) if len(chunk) else 0
                    h = peak * amp
                    x = wave_x0 + 2 + i
                    c.create_line(x, mid_y - h, x, mid_y + h,
                                  fill=LCARS["sunflower"], width=1)

        # Draw trim window overlay
        clip_dur = n / self.audio_sr if self.audio_sr else dur
        if self.trim_r <= 0 or self.trim_r > clip_dur:
            self.trim_r = clip_dur
        if self.trim_l < 0:
            self.trim_l = 0
        if self.trim_l >= self.trim_r:
            self.trim_l = max(0, self.trim_r - 0.05)
        x_l = wave_x0 + 2 + (self.trim_l / clip_dur) * (wave_x1 - wave_x0 - 4)
        x_r = wave_x0 + 2 + (self.trim_r / clip_dur) * (wave_x1 - wave_x0 - 4)
        # dim the parts outside the trim window
        if x_l > wave_x0:
            c.create_rectangle(wave_x0 + 1, wave_top + 1, x_l,
                               wave_top + wave_h - 1,
                               fill="#000000", outline="", stipple="gray50")
        if x_r < wave_x1:
            c.create_rectangle(x_r, wave_top + 1, wave_x1 - 1,
                               wave_top + wave_h - 1,
                               fill="#000000", outline="", stipple="gray50")
        # markers
        c.create_line(x_l, wave_top, x_l, wave_top + wave_h,
                      fill=LCARS["lima"], width=3)
        c.create_line(x_r, wave_top, x_r, wave_top + wave_h,
                      fill=LCARS["lima"], width=3)
        # label trim values
        c.create_text(wave_x0, wave_top + wave_h + 18, anchor="nw",
                      text=f"TRIM  {self.trim_l:.2f}s → {self.trim_r:.2f}s   "
                           f"(KEPT {self.trim_r - self.trim_l:.2f}s)",
                      fill=LCARS["sunflower"], font=self.f_label)

        # Decision-confirmation banner: shown briefly after accept/reject
        # so the user sees the trim+transcript actually persisted before
        # the next clip loads.
        if self._flash_msg:
            fx0, fy0, fx1, fy1 = self._wave_box
            cx = (fx0 + fx1) / 2
            cy = (fy0 + fy1) / 2
            bar_w = min(fx1 - fx0 - 20, 720)
            bar_h = 86
            c.create_rectangle(
                cx - bar_w / 2, cy - bar_h / 2,
                cx + bar_w / 2, cy + bar_h / 2,
                fill=self._flash_color, outline=LCARS["bg"], width=3,
            )
            c.create_text(
                cx, cy, text=self._flash_msg,
                fill=LCARS["bg"], font=self.f_section,
                anchor="center", justify="center",
            )

        # ── TIPS BLOCK (always visible, just above the footer) ───────────
        tips_top = H - 40 - TIPS_H
        c.create_rectangle(0, tips_top, W, H - 40,
                           fill="#080814", outline=LCARS["violet"], width=1)
        col_w = (W - 60) // 3
        col_y = tips_top + 8
        col_xs = [20, 20 + col_w + 10, 20 + 2 * (col_w + 10)]
        col_titles = ["REJECT (0)", "ACCEPT", "TRIM HEAD/TAIL"]
        col_colors = [LCARS["red"], LCARS["lima"], LCARS["bluey"]]
        col_lines = [
            [
                "• Other character speaks anywhere",
                "• SFX over voice (transporter, alarm)",
                "• MELODIC MUSIC OVER THE WORDS",
                "• < 1.2 s speech, > 12 s",
                "• Whisper transcript clearly wrong",
                "• Heavy reverb / phone-EQ episode",
            ],
            [
                "• Bridge / sickbay ambient hum",
                "• Music ONLY between words (no",
                "  overlap with voice itself)",
                "• Subtle non-voice beeps far away",
                "• Light tape hiss",
                "• Majel's natural mid-clause pauses",
            ],
            [
                "• Lead silence > 200 ms → trim head",
                "  to ~100 ms before first phoneme",
                "• Tail silence > 200 ms → trim tail",
                "  to ~150 ms after last phoneme",
                "• Music in pre-speech head → trim out",
                "• Never cut mid-utterance pauses",
            ],
        ]
        for x, title, color, lines in zip(col_xs, col_titles, col_colors, col_lines):
            c.create_text(x, col_y, anchor="nw", text=title,
                          fill=color, font=self.f_label)
            ly = col_y + 18
            for ln in lines:
                c.create_text(x, ly, anchor="nw", text=ln,
                              fill=LCARS["space_white"], font=self.f_mono)
                ly += 16
        # Bottom line: target mix + speed tip.
        c.create_text(W / 2, H - 40 - 42, anchor="center",
                      text="TARGET MIX  60% short status · 20% longer declaratives · 10% questions · 10% alerts",
                      fill=LCARS["sunflower"], font=self.f_label)
        c.create_text(W / 2, H - 40 - 26, anchor="center",
                      text="MUSIC TEST: if you mentally muted Majel and could still hear melody → REJECT",
                      fill=LCARS["butterscotch"], font=self.f_label)
        c.create_text(W / 2, H - 40 - 10, anchor="center",
                      text="FLAG (F or 2) borderline-quality clips you're keeping — fine-tune step can drop or reweight them later",
                      fill=LCARS["bluey"], font=self.f_label)

    # ── Audio + trim ───────────────────────────────────────────────────────
    def _load_clip(self):
        self.audio_data = None  # forces reload in _render
        self.trim_l = 0.0
        self.trim_r = 0.0
        # Stop playback / playhead if user navigates away mid-play.
        self._autoplay = False
        if self._play_proc and self._play_proc.poll() is None:
            try:
                self._play_proc.terminate()
            except Exception:
                pass
        if self._tick_after:
            self.root.after_cancel(self._tick_after)
            self._tick_after = None
        self._playhead_id = None  # _render's c.delete("all") will purge it
        # New clip → drop the stale spell-check line; the next keystroke
        # (or the deferred trigger below) will repopulate it.
        self._spell_msg = ""
        if self._spell_after:
            self.root.after_cancel(self._spell_after)
            self._spell_after = None
        # Run an immediate spell check on the new clip's default transcript
        # so the user sees status without having to type first.
        self._spell_after = self.root.after(50, self._update_spell)

    def _play(self):
        if self.idx >= len(self.clips):
            return
        if self._play_proc and self._play_proc.poll() is None:
            self._play_proc.terminate()
        if self._tick_after:
            self.root.after_cancel(self._tick_after)
            self._tick_after = None
        path = self.clips[self.idx]
        clip_dur = len(self.audio_data) / self.audio_sr if self.audio_data is not None else 0
        # If trim is set, generate a temp clip via ffmpeg and play it.
        if (self.trim_l > 0.01 or
                (self.trim_r > 0 and self.trim_r < clip_dur - 0.01)):
            tmp = Path("/tmp/curate_play.wav")
            d = self.trim_r - self.trim_l
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-ss", f"{self.trim_l:.3f}", "-t", f"{d:.3f}",
                 "-i", str(path), str(tmp)],
                check=True,
            )
            target = tmp
            self._play_offset = self.trim_l
            self._play_dur = max(0.05, d)
        else:
            target = path
            self._play_offset = 0.0
            self._play_dur = max(0.05, clip_dur)
        env = {"XDG_RUNTIME_DIR": f"/run/user/{__import__('os').getuid()}"}
        env.update(__import__("os").environ)
        self._play_proc = subprocess.Popen(
            ["paplay", str(target)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env,
        )
        self._play_start_t = time.monotonic()
        self._tick_playhead()

    def _tick_playhead(self):
        """Sliding cursor across the waveform during playback."""
        if not hasattr(self, "_wave_box") or self.audio_data is None:
            return
        elapsed = time.monotonic() - self._play_start_t
        # Stop when audio is done or process exited.
        proc_done = (self._play_proc is None or
                     self._play_proc.poll() is not None)
        if elapsed >= self._play_dur or proc_done:
            if self._playhead_id is not None:
                try:
                    self.canvas.delete(self._playhead_id)
                except tk.TclError:
                    pass
                self._playhead_id = None
            self._tick_after = None
            # Loop while the user is still actively typing in the
            # transcript field — gives them an audible reference as
            # they retype the line.
            if (self._autoplay and
                    self.root.focus_get() is self.transcript_entry):
                self._tick_after = self.root.after(220, self._play)
            return
        # Compute playhead x position in canvas coords.
        x0, y0, x1, y1 = self._wave_box
        clip_dur = len(self.audio_data) / self.audio_sr
        if clip_dur <= 0:
            self._tick_after = self.root.after(60, self._tick_playhead)
            return
        # Position within the FULL clip: trim offset + elapsed
        cur_t = self._play_offset + elapsed
        cur_t = max(0.0, min(clip_dur, cur_t))
        x = x0 + 2 + (cur_t / clip_dur) * (x1 - x0 - 4)
        if self._playhead_id is None:
            self._playhead_id = self.canvas.create_line(
                x, y0, x, y1, fill=LCARS["orange"], width=2)
        else:
            try:
                self.canvas.coords(self._playhead_id, x, y0, x, y1)
            except tk.TclError:
                self._playhead_id = None
        self._tick_after = self.root.after(33, self._tick_playhead)

    def _wave_press(self, e):
        x0, y0, x1, y1 = self._wave_box
        if not (y0 <= e.y <= y1 and x0 <= e.x <= x1):
            return
        clip_dur = (len(self.audio_data) / self.audio_sr
                    if self.audio_data is not None else 0)
        if clip_dur <= 0:
            return
        span = x1 - x0 - 4
        t = (e.x - x0 - 2) / span * clip_dur
        t = max(0.0, min(clip_dur, t))
        # The right marker stored as 0 means "use clip end" — normalise
        # for the midpoint calculation.
        cur_r = self.trim_r if self.trim_r > 0 else clip_dur
        mid = (self.trim_l + cur_r) / 2
        # Click in the LEFT half of the trim window → grab the left
        # marker; click in the RIGHT half → grab the right marker.
        # The other marker stays put — we no longer reset the window.
        self._drag_marker = "L" if t < mid else "R"
        self._drag_active = True
        if self._drag_marker == "L":
            self.trim_l = min(t, cur_r - 0.05)
            if self.trim_r <= 0:
                self.trim_r = clip_dur
        else:
            self.trim_r = max(t, self.trim_l + 0.05)
        self._render()

    def _wave_drag(self, e):
        if not self._drag_active or self._drag_marker is None:
            return
        x0, _, x1, _ = self._wave_box
        clip_dur = (len(self.audio_data) / self.audio_sr
                    if self.audio_data is not None else 0)
        if clip_dur <= 0:
            return
        span = x1 - x0 - 4
        t = (e.x - x0 - 2) / span * clip_dur
        t = max(0.0, min(clip_dur, t))
        cur_r = self.trim_r if self.trim_r > 0 else clip_dur
        if self._drag_marker == "L":
            self.trim_l = min(t, cur_r - 0.05)
        else:
            self.trim_r = max(t, self.trim_l + 0.05)
        self._render()

    def _wave_release(self, _e):
        self._drag_active = False
        self._drag_marker = None

    def _reset_trim(self):
        self.trim_l = 0.0
        self.trim_r = 0.0
        self._render()

    # ── Decisions ─────────────────────────────────────────────────────────
    def _set_flash(self, msg: str, color: str) -> None:
        """Show an in-canvas confirmation banner immediately."""
        self._flash_msg = msg
        self._flash_color = color
        self._render()
        # Force a paint so the banner is visible even when the next
        # operation (ffmpeg) blocks the event loop.
        self.root.update_idletasks()

    def _schedule_advance(self, delay_ms: int = 750) -> None:
        if self._flash_after:
            self.root.after_cancel(self._flash_after)
        self._flash_after = self.root.after(delay_ms, self._advance_after_flash)

    def _advance_after_flash(self) -> None:
        self._flash_msg = None
        self._flash_after = None
        self._next()

    def _clear_flash(self) -> None:
        if self._flash_after:
            self.root.after_cancel(self._flash_after)
            self._flash_after = None
        self._flash_msg = None

    def _accept(self):
        if self.idx >= len(self.clips):
            return
        path = self.clips[self.idx]
        rank, cos, dur, transcript = parse_filename(path)
        clip_dur = (len(self.audio_data) / self.audio_sr
                    if self.audio_data is not None else dur)
        actual_dur = self.trim_r - self.trim_l if self.trim_r > 0 else clip_dur
        was_trimmed = (self.trim_l > 0.01 or
                       (self.trim_r > 0 and self.trim_r < clip_dur - 0.01))

        # Phase 1 — visible "in-flight" banner so the user sees ffmpeg
        # actually trimming + the manifest write before we advance.
        self._set_flash(
            "TRIMMING & SAVING…" if was_trimmed else "SAVING…",
            LCARS["orange"],
        )

        CURATED_DIR.mkdir(parents=True, exist_ok=True)
        out = CURATED_DIR / path.name

        if was_trimmed:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-ss", f"{self.trim_l:.3f}", "-t", f"{actual_dur:.3f}",
                 "-i", str(path),
                 "-ar", "22050", "-ac", "1", "-c:a", "pcm_s16le", str(out)],
                check=True,
            )
        else:
            shutil.copy(path, out)

        # User-edited transcript wins over the parsed-slug default.
        final_transcript = (self.transcript_var.get().strip()
                            or self.transcript_overrides.get(path.name, transcript))
        # Force-flush any pending debounced override save so the on-disk
        # transcript_overrides.json matches what we just wrote to manifest.
        if self._transcript_save_after:
            self.root.after_cancel(self._transcript_save_after)
            self._transcript_save_after = None
        save_transcript_overrides(self.transcript_overrides)

        flag_state = self.flag_overrides.get(path.name, {"flagged": False, "note": ""})
        append_jsonl(MANIFEST_ACCEPT, {
            "clip_id": path.name,
            "src": path.name,
            "out": out.name,
            "rank": rank,
            "cosine": cos,
            "duration": round(actual_dur, 3),
            "trim_start": round(self.trim_l, 3),
            "trim_end": round(self.trim_r, 3),
            "transcript": final_transcript,
            "flagged": bool(flag_state.get("flagged")),
            "flag_note": flag_state.get("note", ""),
        })
        self.accepted.add(path.name)

        # Phase 2 — confirmation banner with the actual trim window and
        # final transcript that landed in the manifest.
        trim_str = (f"{self.trim_l:.2f}s → {self.trim_r:.2f}s"
                    if was_trimmed else "FULL CLIP")
        ts_short = final_transcript if len(final_transcript) <= 80 else final_transcript[:79] + "…"
        flag_tag = "  ⚑ FLAGGED" if flag_state.get("flagged") else ""
        self._set_flash(
            f"✓ ACCEPTED{flag_tag}\n"
            f"TRIMMED  {trim_str}\n"
            f"TRANSCRIPT  '{ts_short}'",
            LCARS["lima"],
        )
        self._schedule_advance(820)

    def _reject(self):
        if self.idx >= len(self.clips):
            return
        path = self.clips[self.idx]
        rank, cos, dur, transcript = parse_filename(path)
        append_jsonl(MANIFEST_REJECT, {
            "clip_id": path.name,
            "rank": rank,
            "cosine": cos,
            "duration": dur,
            "transcript": transcript,
        })
        self.rejected.add(path.name)
        self._set_flash("✗ REJECTED", LCARS["red"])
        self._schedule_advance(450)

    def _next(self):
        self._clear_flash()
        self.idx = min(len(self.clips), self.idx + 1)
        # Skip already-decided clips
        while (self.idx < len(self.clips) and
               (self.clips[self.idx].name in self.accepted or
                self.clips[self.idx].name in self.rejected)):
            self.idx += 1
        self._load_clip()
        self._render()

    def _prev(self):
        self._clear_flash()
        self.idx = max(0, self.idx - 1)
        self._load_clip()
        self._render()


def main():
    if not RANKED_DIR.exists():
        sys.stderr.write(f"Missing {RANKED_DIR}. Run scripts/export_top_clips.py first.\n")
        sys.exit(2)
    root = tk.Tk()
    Curator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
