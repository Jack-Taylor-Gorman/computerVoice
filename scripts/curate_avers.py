#!/usr/bin/env python3
"""BoTNS-themed clip curator for the Severian / Roy Avers fine-tune dataset.

Forked from `scripts/curate_clips.py` (the Majel/LCARS curator) — same
keyboard-driven approve/flag/trim workflow, but recoloured for the Citadel
of the Autarch and with an explicit DELETE action (`D` key) that removes
the source wav from disk so the candidate list shrinks as you go.

Walks `dataset_avers/clips_ranked/*.wav` (sorted by composite quality score),
plays each clip on demand, lets the human accept / accept-flag / reject /
delete / trim with keyboard shortcuts, and emits three manifests:

  dataset_avers/manifest_curated.jsonl   accepted
  dataset_avers/manifest_rejected.jsonl  rejected (skipped, no disk impact)
  dataset_avers/manifest_deleted.jsonl   deleted (wav removed from disk)

Accepted clips are also copied to `dataset_avers/clips_curated/` (with the
trim window applied if any), so the F5-TTS fine-tuner can point at one
folder.

Resume: the UI reads all three manifests on launch and skips already-decided
clip IDs, so you can quit and resume mid-session.

Keyboard:
  Space  play / replay
  ↑      keep clip (with current trim window)
  ↓      cast out (move wav to clips_trash/)
  ←      previous clip
  →      next clip (skips already-decided)
  0      pass (keep file on disk, skip for training)
  R      reset trim window
  Q      quit

Auto-play: any time you move to a new clip (via ←/→ or via the auto-advance
that fires after a decision), the new clip is played automatically.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

import numpy as np
import soundfile as sf
from spellchecker import SpellChecker

# Book of the New Sun proper-noun vocabulary so the spellchecker doesn't
# flag every "autarch" / "Severian" / "fuligin" in the transcripts.
BOTNS_VOCAB = (
    "severian autarch hierodule hierogrammate optimate armiger exultant "
    "autochthon eidolon pelerine matachin citadel fuligin terminus est "
    "drott eata roche cyriaca thecla baldanders agia agilus dorcas hethor "
    "gurloes palaemon malrubius typhon vodalus dr ascia conciliator increate "
    "anpiel sabretache nessus thrax brigand alzabo apu punchau urth lune "
    "wolfe gene roy avers narrator audiobook louisville kentucky "
).split()

ROOT = Path(__file__).resolve().parent.parent
RANKED_DIR = ROOT / "dataset_avers" / "clips_ranked"
RANKED_METADATA = RANKED_DIR / "_metadata.jsonl"
CURATED_DIR = ROOT / "dataset_avers" / "clips_curated"
TRASH_DIR = ROOT / "dataset_avers" / "clips_trash"
MANIFEST_ACCEPT = ROOT / "dataset_avers" / "manifest_curated.jsonl"
MANIFEST_REJECT = ROOT / "dataset_avers" / "manifest_rejected.jsonl"
MANIFEST_DELETE = ROOT / "dataset_avers" / "manifest_deleted.jsonl"
TRANSCRIPT_OVERRIDES = ROOT / "dataset_avers" / "transcript_overrides.json"
FLAG_OVERRIDES = ROOT / "dataset_avers" / "flag_overrides.json"


def load_ranked_metadata() -> dict[str, str]:
    """Map ranked filename → full WhisperX transcript (not the 6-word slug)."""
    out: dict[str, str] = {}
    if RANKED_METADATA.exists():
        for line in RANKED_METADATA.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get("filename")
            text = row.get("text")
            if name and text:
                out[name] = text
    return out

# BoTNS palette — Citadel of the Autarch, parchment and candle-gold on dim
# brown. Matches the rebranded majel_gui.py.
THEME = {
    "bg":             "#1a0f08",   # deep brown-black
    "panel":          "#2a1a0f",
    "violet":         "#8a6a3a",   # dim bronze (was LCARS violet)
    "orange":         "#c9a063",   # warm bronze / parchment-gold (primary accent)
    "butterscotch":   "#d8a878",   # softer parchment-gold
    "sunflower":      "#e8c98a",   # lit candle-gold (section headings)
    "red":            "#c4521a",   # rust (warnings, deletions)
    "bluey":          "#8a8a6a",   # dim sage (info)
    "lima":           "#a8c468",   # mossy green for accept
    "space_white":    "#e0d0a8",   # parchment ink (text)
    "wave_bg":        "#0e0807",   # deep waveform background
}

WIN_W = 1280
WIN_H = 780
TIPS_H = 90


def botns_font(size: int = 12, weight: str = "bold") -> tkfont.Font:
    candidates = ("EB Garamond", "Garamond", "DejaVu Serif",
                  "Liberation Serif", "Times New Roman", "Times")
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


def load_decided() -> tuple[set[str], set[str], set[str]]:
    """Return (accepted, rejected, deleted) clip-id sets."""
    accepted, rejected, deleted = set(), set(), set()
    for path, bag in ((MANIFEST_ACCEPT, accepted),
                      (MANIFEST_REJECT, rejected),
                      (MANIFEST_DELETE, deleted)):
        if path.exists():
            for line in path.read_text().splitlines():
                try:
                    bag.add(json.loads(line)["clip_id"])
                except (json.JSONDecodeError, KeyError):
                    continue
    return accepted, rejected, deleted


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
        root.title("SEVERIAN · CLIP CHRONICLE")
        root.geometry(f"{WIN_W}x{WIN_H}")
        root.configure(bg=THEME["bg"])
        root.minsize(800, 480)

        self.f_title = botns_font(20, "bold")
        self.f_section = botns_font(13, "bold")
        self.f_label = botns_font(11, "bold")
        self.f_mono = tkfont.Font(family="DejaVu Sans Mono", size=10)

        self.canvas = tk.Canvas(root, bg=THEME["bg"],
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", self._on_resize)

        self.clips: list[Path] = sorted(RANKED_DIR.glob("*.wav"),
                                        key=lambda p: parse_filename(p)[0])
        self.accepted, self.rejected, self.deleted = load_decided()
        self.transcript_overrides: dict[str, str] = load_transcript_overrides()
        self.flag_overrides: dict[str, dict] = load_flag_overrides()
        # Full WhisperX transcripts from the ranking step. Curator prefers
        # these over the 6-word filename slug, which gets truncated mid-clip.
        self.full_transcripts: dict[str, str] = load_ranked_metadata()
        self.idx = self._first_undecided()

        self.transcript_var = tk.StringVar()
        self.transcript_entry = tk.Entry(
            self.canvas, textvariable=self.transcript_var,
            bg=THEME["panel"], fg=THEME["space_white"],
            insertbackground=THEME["sunflower"],
            relief="flat", bd=0, font=self.f_label,
            highlightthickness=2, highlightbackground=THEME["violet"],
            highlightcolor=THEME["orange"],
        )
        self.transcript_entry.bind("<Return>", lambda e: self._commit_transcript_focus())
        self.transcript_entry.bind("<KP_Enter>", lambda e: self._commit_transcript_focus())
        self.transcript_entry.bind("<FocusOut>", lambda e: self._stop_autoplay())
        self._transcript_save_after: str | None = None
        self._suppress_change_handler: bool = False
        self.transcript_var.trace_add("write", self._on_transcript_changed)

        self._spell = SpellChecker()
        self._spell.word_frequency.load_words(BOTNS_VOCAB)
        self._spell_msg: str = ""
        self._spell_after: str | None = None

        self._autoplay: bool = False
        self.trim_l: float = 0.0
        self.trim_r: float = 0.0
        self.audio_data: np.ndarray | None = None
        self.audio_sr: int = 0
        self._play_proc: subprocess.Popen | None = None
        self._play_start_t: float = 0.0
        self._play_dur: float = 0.0
        self._play_offset: float = 0.0
        self._playhead_id: int | None = None
        self._tick_after: str | None = None

        def gate(handler):
            def wrapped(e):
                if root.focus_get() is self.transcript_entry:
                    return None
                return handler()
            return wrapped
        root.bind("<space>", gate(self._play))
        root.bind("<Up>", gate(self._accept))         # keep
        root.bind("<Down>", gate(self._delete))       # cast out
        root.bind("<Left>", gate(self._prev))         # previous clip
        root.bind("<Right>", gate(self._next))        # next clip
        root.bind("0", gate(self._reject))            # pass (skip for training)
        root.bind("r", gate(self._reset_trim))
        root.bind("q", gate(lambda: root.destroy()))

        self.canvas.bind("<ButtonPress-1>", self._wave_press)
        self.canvas.bind("<B1-Motion>", self._wave_drag)
        self.canvas.bind("<ButtonRelease-1>", self._wave_release)
        self._drag_active = False
        self._drag_marker: str | None = None

        self._flash_msg: str | None = None
        self._flash_color: str = THEME["lima"]
        self._flash_after: str | None = None

        self._render()

    def _first_undecided(self) -> int:
        for i, p in enumerate(self.clips):
            if (p.name not in self.accepted and
                p.name not in self.rejected and
                p.name not in self.deleted):
                return i
        return 0

    def _on_resize(self, _ev):
        self._render()

    def _commit_transcript_focus(self):
        self.canvas.focus_set()

    def _on_transcript_changed(self, *_args):
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
        if self._spell_after:
            self.root.after_cancel(self._spell_after)
        self._spell_after = self.root.after(250, self._update_spell)
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
        self._autoplay = False

    def _current_transcript(self) -> str:
        if self.idx >= len(self.clips):
            return ""
        clip_id = self.clips[self.idx].name
        if clip_id in self.transcript_overrides:
            return self.transcript_overrides[clip_id]
        # Prefer the full WhisperX transcript from the rank-time side
        # manifest. Fall back to the 6-word filename slug only if the side
        # manifest is missing (older clips_ranked/ directories).
        if clip_id in self.full_transcripts:
            return self.full_transcripts[clip_id]
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

        # Header
        c.create_rectangle(0, 0, W, 40, fill=THEME["orange"], outline=THEME["orange"])
        c.create_text(20, 20, anchor="w", text="SEVERIAN · CLIP CHRONICLE",
                      fill=THEME["bg"], font=self.f_title)
        # Counters
        n_total = len(self.clips)
        n_acc = len(self.accepted)
        n_rej = len(self.rejected)
        n_del = len(self.deleted)
        n_left = n_total - n_acc - n_rej - n_del
        acc_dur = total_duration(MANIFEST_ACCEPT)
        c.create_text(W - 20, 20, anchor="e",
                      text=(f"KEPT {n_acc}   PASSED {n_rej}   CAST OUT {n_del}   "
                            f"REMAINING {n_left}   ·   {acc_dur/60:.1f} MIN"),
                      fill=THEME["bg"], font=self.f_label)

        # Footer hotkeys
        c.create_rectangle(0, H - 40, W, H, fill=THEME["sunflower"], outline=THEME["sunflower"])
        c.create_text(W / 2, H - 20, anchor="center",
                      text=("SPACE play   ↑ keep   ↓ cast out   ← prev   → next   "
                            "0 pass   R reset trim   Q quit"),
                      fill=THEME["bg"], font=self.f_label)

        if not self.clips:
            c.create_text(W/2, H/2,
                          text="NO CLIPS IN dataset_avers/clips_ranked/  ·  RUN scripts/rank_avers.py FIRST",
                          fill=THEME["sunflower"], font=self.f_section)
            return

        if self.idx >= len(self.clips):
            c.create_text(W/2, H/2, text="ALL CLIPS DECIDED · QUIT WITH Q",
                          fill=THEME["sunflower"], font=self.f_title)
            return

        p = self.clips[self.idx]
        rank, cos, dur, transcript = parse_filename(p)

        y = 60
        decision = "AWAITING"
        decision_color = THEME["sunflower"]
        if p.name in self.accepted:
            decision, decision_color = "KEPT", THEME["lima"]
        elif p.name in self.rejected:
            decision, decision_color = "PASSED", THEME["bluey"]
        elif p.name in self.deleted:
            decision, decision_color = "CAST OUT", THEME["red"]

        c.create_text(20, y, anchor="nw",
                      text=f"CLIP {self.idx+1} / {len(self.clips)}",
                      fill=THEME["sunflower"], font=self.f_section)
        right_x = W - 20
        c.create_text(right_x, y, anchor="ne", text=decision,
                      fill=decision_color, font=self.f_section)
        y += 26
        c.create_text(20, y, anchor="nw",
                      text=f"SCORE {cos:.3f}   DURATION {dur:.1f}s   RANK {rank}",
                      fill=THEME["violet"], font=self.f_label)
        y += 22
        c.create_text(20, y, anchor="nw",
                      text=f"FILE  {p.name}",
                      fill=THEME["space_white"], font=self.f_mono)
        y += 22

        c.create_text(20, y, anchor="nw", text="TRANSCRIPT",
                      fill=THEME["sunflower"], font=self.f_label)
        target = self._current_transcript()
        if self.transcript_var.get() != target:
            self._suppress_change_handler = True
            try:
                self.transcript_var.set(target)
            finally:
                self._suppress_change_handler = False
        entry_x = 130
        entry_w = max(200, W - entry_x - 24)
        c.create_window(entry_x, y - 2, anchor="nw", window=self.transcript_entry,
                        width=entry_w, height=26)
        y += 28
        if self._spell_msg:
            ok = self._spell_msg.startswith("✓")
            c.create_text(entry_x, y, anchor="nw", text=self._spell_msg,
                          fill=(THEME["lima"] if ok else THEME["butterscotch"]),
                          font=self.f_mono)
        y += 18

        wave_top = y + 4
        wave_h = H - 40 - TIPS_H - wave_top - 60
        wave_x0, wave_x1 = 20, W - 20
        c.create_rectangle(wave_x0, wave_top, wave_x1, wave_top + wave_h,
                           fill=THEME["wave_bg"], outline=THEME["violet"], width=2)
        self._wave_box = (wave_x0, wave_top, wave_x1, wave_top + wave_h)

        if self.audio_data is None:
            try:
                self.audio_data, self.audio_sr = sf.read(str(p), dtype="float32")
                if self.audio_data.ndim > 1:
                    self.audio_data = self.audio_data.mean(axis=1)
            except Exception:
                self.audio_data = np.zeros(1, dtype=np.float32)
                self.audio_sr = 22050

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
                                  fill=THEME["sunflower"], width=1)

        clip_dur = n / self.audio_sr if self.audio_sr else dur
        if self.trim_r <= 0 or self.trim_r > clip_dur:
            self.trim_r = clip_dur
        if self.trim_l < 0:
            self.trim_l = 0
        if self.trim_l >= self.trim_r:
            self.trim_l = max(0, self.trim_r - 0.05)
        x_l = wave_x0 + 2 + (self.trim_l / clip_dur) * (wave_x1 - wave_x0 - 4)
        x_r = wave_x0 + 2 + (self.trim_r / clip_dur) * (wave_x1 - wave_x0 - 4)
        if x_l > wave_x0:
            c.create_rectangle(wave_x0 + 1, wave_top + 1, x_l,
                               wave_top + wave_h - 1,
                               fill="#000000", outline="", stipple="gray50")
        if x_r < wave_x1:
            c.create_rectangle(x_r, wave_top + 1, wave_x1 - 1,
                               wave_top + wave_h - 1,
                               fill="#000000", outline="", stipple="gray50")
        c.create_line(x_l, wave_top, x_l, wave_top + wave_h,
                      fill=THEME["lima"], width=3)
        c.create_line(x_r, wave_top, x_r, wave_top + wave_h,
                      fill=THEME["lima"], width=3)
        c.create_text(wave_x0, wave_top + wave_h + 18, anchor="nw",
                      text=(f"TRIM  {self.trim_l:.2f}s → {self.trim_r:.2f}s   "
                            f"(KEPT {self.trim_r - self.trim_l:.2f}s)"),
                      fill=THEME["sunflower"], font=self.f_label)

        if self._flash_msg:
            fx0, fy0, fx1, fy1 = self._wave_box
            cx = (fx0 + fx1) / 2
            cy = (fy0 + fy1) / 2
            bar_w = min(fx1 - fx0 - 20, 720)
            bar_h = 86
            c.create_rectangle(
                cx - bar_w / 2, cy - bar_h / 2,
                cx + bar_w / 2, cy + bar_h / 2,
                fill=self._flash_color, outline=THEME["bg"], width=3,
            )
            c.create_text(
                cx, cy, text=self._flash_msg,
                fill=THEME["bg"], font=self.f_section,
                anchor="center", justify="center",
            )

        # ── TIPS STRIP — compact icon-led legend ──────────────────────────
        tips_top = H - 40 - TIPS_H
        c.create_rectangle(0, tips_top, W, H - 40,
                           fill=THEME["panel"], outline=THEME["violet"], width=1)
        col_w = (W - 60) // 3
        col_xs = [20, 20 + col_w + 10, 20 + 2 * (col_w + 10)]
        # Three columns: cast out / keep / trim. Each column = a big icon-glyph
        # row at the top (one line, larger font), and a 3-line legend below
        # using symbols instead of prose.
        f_icon = botns_font(20, "bold")
        col_data = [
            (THEME["lima"], "↑  KEEP",
             ["♫  clean narrator tone",
              "·  ends   .  ?  !",
              "→  next  ·  ←  prev"]),
            (THEME["red"], "↓  CAST OUT",
             ["⌛  < 4 s   or   > 14 s",
              "abc  starts lowercase",
              "♪  dialogue / NLS"]),
            (THEME["bluey"], "↔  TRIM  ·  drag  /  R reset",
             ["⌜⌝  green markers = bounds",
              "▶  SPACE  play / replay",
              "0  pass (keep file, skip)"]),
        ]
        for x, (color, title, lines) in zip(col_xs, col_data):
            c.create_text(x, tips_top + 4, anchor="nw", text=title,
                          fill=color, font=f_icon)
            ly = tips_top + 32
            for ln in lines:
                c.create_text(x, ly, anchor="nw", text=ln,
                              fill=THEME["space_white"], font=self.f_mono)
                ly += 16

    # ── Audio + trim ───────────────────────────────────────────────────────
    def _load_clip(self):
        self.audio_data = None
        self.trim_l = 0.0
        self.trim_r = 0.0
        self._autoplay = False
        if self._play_proc and self._play_proc.poll() is None:
            try:
                self._play_proc.terminate()
            except Exception:
                pass
        if self._tick_after:
            self.root.after_cancel(self._tick_after)
            self._tick_after = None
        self._playhead_id = None
        self._spell_msg = ""
        if self._spell_after:
            self.root.after_cancel(self._spell_after)
            self._spell_after = None
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
        if not hasattr(self, "_wave_box") or self.audio_data is None:
            return
        elapsed = time.monotonic() - self._play_start_t
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
            if (self._autoplay and
                    self.root.focus_get() is self.transcript_entry):
                self._tick_after = self.root.after(220, self._play)
            return
        x0, y0, x1, y1 = self._wave_box
        clip_dur = len(self.audio_data) / self.audio_sr
        if clip_dur <= 0:
            self._tick_after = self.root.after(60, self._tick_playhead)
            return
        cur_t = self._play_offset + elapsed
        cur_t = max(0.0, min(clip_dur, cur_t))
        x = x0 + 2 + (cur_t / clip_dur) * (x1 - x0 - 4)
        if self._playhead_id is None:
            self._playhead_id = self.canvas.create_line(
                x, y0, x, y1, fill=THEME["orange"], width=2)
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
        cur_r = self.trim_r if self.trim_r > 0 else clip_dur
        mid = (self.trim_l + cur_r) / 2
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
        self._flash_msg = msg
        self._flash_color = color
        self._render()
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

        self._set_flash(
            "TRIMMING & KEEPING…" if was_trimmed else "KEEPING…",
            THEME["orange"],
        )

        CURATED_DIR.mkdir(parents=True, exist_ok=True)
        out = CURATED_DIR / path.name
        if was_trimmed:
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error",
                 "-ss", f"{self.trim_l:.3f}", "-t", f"{actual_dur:.3f}",
                 "-i", str(path),
                 "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", str(out)],
                check=True,
            )
        else:
            shutil.copy(path, out)

        final_transcript = (self.transcript_var.get().strip()
                            or self.transcript_overrides.get(path.name, transcript))
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
            "score": cos,
            "duration": round(actual_dur, 3),
            "trim_start": round(self.trim_l, 3),
            "trim_end": round(self.trim_r, 3),
            "transcript": final_transcript,
            "flagged": bool(flag_state.get("flagged")),
            "flag_note": flag_state.get("note", ""),
        })
        self.accepted.add(path.name)

        trim_str = (f"{self.trim_l:.2f}s → {self.trim_r:.2f}s"
                    if was_trimmed else "FULL CLIP")
        ts_short = final_transcript if len(final_transcript) <= 80 else final_transcript[:79] + "…"
        flag_tag = "  ⚑ FLAGGED" if flag_state.get("flagged") else ""
        self._set_flash(
            f"✓ KEPT{flag_tag}\n"
            f"TRIMMED  {trim_str}\n"
            f"TRANSCRIPT  '{ts_short}'",
            THEME["lima"],
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
            "score": cos,
            "duration": dur,
            "transcript": transcript,
        })
        self.rejected.add(path.name)
        self._set_flash("✗ PASSED", THEME["bluey"])
        self._schedule_advance(420)

    def _delete(self):
        """Remove the clip's wav from disk (move to clips_trash/) and record
        the decision. Recoverable via `mv dataset_avers/clips_trash/* dataset_avers/clips_ranked/`.
        """
        if self.idx >= len(self.clips):
            return
        path = self.clips[self.idx]
        rank, cos, dur, transcript = parse_filename(path)
        TRASH_DIR.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(path), str(TRASH_DIR / path.name))
        except (OSError, shutil.Error) as e:
            self._set_flash(f"✗ DELETE FAILED\n{e}", THEME["red"])
            self._schedule_advance(1200)
            return
        append_jsonl(MANIFEST_DELETE, {
            "clip_id": path.name,
            "rank": rank,
            "score": cos,
            "duration": dur,
            "transcript": transcript,
            "trash_path": str(TRASH_DIR / path.name),
        })
        self.deleted.add(path.name)
        self._set_flash("☠ CAST OUT\n(moved to clips_trash/)", THEME["red"])
        self._schedule_advance(820)

    def _next(self):
        self._clear_flash()
        self.idx = min(len(self.clips), self.idx + 1)
        while (self.idx < len(self.clips) and
               (self.clips[self.idx].name in self.accepted or
                self.clips[self.idx].name in self.rejected or
                self.clips[self.idx].name in self.deleted)):
            self.idx += 1
        self._load_clip()
        self._render()
        self._autoplay_new_clip()

    def _prev(self):
        self._clear_flash()
        self.idx = max(0, self.idx - 1)
        self._load_clip()
        self._render()
        self._autoplay_new_clip()

    def _autoplay_new_clip(self):
        """Play the current clip after a navigation move. Skipped if we've
        run off the end of the list, or if the audio failed to load."""
        if self.idx >= len(self.clips):
            return
        # Small delay so the canvas finishes rendering before paplay starts.
        self.root.after(80, self._play)


def main():
    if not RANKED_DIR.exists():
        sys.stderr.write(f"Missing {RANKED_DIR}. Run scripts/rank_avers.py first.\n")
        sys.exit(2)
    root = tk.Tk()
    Curator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
