#!/usr/bin/env python3
"""LCARS-themed Tk control panel for the Majel computer-voice system.

Visual reference: https://www.thelcars.com/ (canonical Okudagram palette and
geometry; CSS source `assets/classic-260430.css`).

Live controls:
  - Voice enable, music enable, enter-SFX enable.
  - Voice mode: offline (templates) or Claude API (context-aware rewriter).
  - Anthropic API key entry + test.
  - Background music group + skip-track.
  - Audio sliders.
  - Service status with pill-style ONLINE/OFFLINE indicators.
  - Direct-chat panel: type a message, hear Majel respond (uses API mode).

The decorative bottom bar shows live useful info: clock, voice mode,
trekdata clip count, services online count.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parent
CONFIG = Path.home() / ".majel_config.json"
BG_PID = ROOT / ".background.pid"
BG_DIR = ROOT / "sounds" / "background"
TREK_DB = ROOT / "storage" / "trekdata.sqlite"

DAEMONS = {
    "background.py":   "BACKGROUND AMBIENT",
    "voice_sfx.py":    "KEYBOARD SFX",
    "f5_daemon.py":    "F5-TTS VOICE DAEMON",
    "tty_watcher.py":  "APPROVAL CHIME",
    "loop_watcher.py": "STUCK-LOOP DETECTOR",
}

DEFAULT_CFG = {
    "voice_enabled": True,
    "bg_enabled": True,
    "bg_group": "",
    "bg_mode": "loop",       # "sequence" | "loop" | "random"
    "bg_volume": 25,
    "duck_volume": 6,
    "enter_sound": True,
    "voice_mode": "offline",
    "anthropic_api_key": "",
    "narrate_during_build": False,  # PostToolUse step narration on/off
    "loop_watcher_enabled": False,  # Stuck-loop detector on/off
}

# ── LCARS palette (verbatim from thelcars.com) ───────────────────────────────
LCARS = {
    "bg":              "#000000",
    "african_violet":  "#ccaaff",
    "moonlit_violet":  "#9966ff",
    "lilac":           "#cc55ff",
    "violet_creme":    "#ddbbff",
    "orange":          "#ff8800",
    "golden_orange":   "#ff9900",
    "butterscotch":    "#ff9966",
    "sunflower":       "#ffcc99",
    "peach":           "#ff8866",
    "almond_creme":    "#ffbbaa",
    "red":             "#cc4444",
    "tomato":          "#ff5555",
    "mars":            "#ff2200",
    "bluey":           "#8899ff",
    "blue":            "#5566ff",
    "sky":             "#aaaaff",
    "ice":             "#99ccff",
    "lima_bean":       "#cccc66",
    "space_white":     "#f5f6fa",
}

GUTTER = 4
BAR_H = 32
RAIL_W = 168
ELBOW_R = 80
ELBOW_H = 96                          # vertical span of top/bottom elbows
WIN_W, WIN_H = 1100, 1180  # bumped to fit BRIEFING section without overflowing the bottom elbow
MIN_W, MIN_H = 1000, 920

CONTENT_X = RAIL_W + GUTTER + 28      # left edge of content area, well clear of elbow curve
# Content y0 sits a few px below the elbow's bottom edge so the first content
# section (VOICE OPERATIONS) lines up with the start of the VOICE rail panel.
CONTENT_Y = ELBOW_H + 14

FONT_CHAIN = (
    "Antonio", "Helvetica Inserat", "Swiss 911 Ultra Compressed",
    "DejaVu Sans Condensed", "Liberation Sans Narrow", "Helvetica",
)


def lcars_font(size: int = 12, weight: str = "bold") -> tkfont.Font:
    avail = set(tkfont.families())
    for name in FONT_CHAIN:
        if name in avail:
            return tkfont.Font(family=name, size=size, weight=weight)
    return tkfont.Font(size=size, weight=weight)


# ── Config + daemon helpers ─────────────────────────────────────────────────
def load_cfg() -> dict:
    if CONFIG.exists():
        try:
            return {**DEFAULT_CFG, **json.loads(CONFIG.read_text())}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(DEFAULT_CFG)


def save_cfg(cfg: dict) -> None:
    tmp = CONFIG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(CONFIG)


def list_bg_groups() -> list[str]:
    if not BG_DIR.exists():
        return []
    groups: set[str] = set()
    for p in BG_DIR.glob("*.mp3"):
        if p.name.startswith("tos_"):
            continue
        base = re.sub(r"_\d+$", "", p.stem)
        if base == "ds9_promenade":
            continue
        groups.add(base)
    return sorted(groups)


def bg_ffplay_sink_id() -> str | None:
    try:
        pid_s = BG_PID.read_text().strip()
    except OSError:
        return None
    try:
        r = subprocess.run(["pactl", "list", "sink-inputs"],
                           capture_output=True, text=True, timeout=1)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    current_id = None
    first_ffplay: str | None = None
    for line in r.stdout.splitlines():
        line = line.strip()
        if line.startswith("Sink Input #"):
            current_id = line.split("#", 1)[1]
        elif line.startswith("application.name") and '"ffplay"' in line:
            if first_ffplay is None:
                first_ffplay = current_id
        elif line.startswith("application.process.id"):
            if pid_s in line:
                return current_id
    return first_ffplay


def set_bg_volume(pct: int) -> None:
    sid = bg_ffplay_sink_id()
    if not sid:
        return
    subprocess.run(["pactl", "set-sink-input-volume", sid, f"{pct}%"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1)


def is_running(script: str) -> bool:
    try:
        r = subprocess.run(["pgrep", "-f", f"/{script}$"], capture_output=True, text=True)
        return bool(r.stdout.strip())
    except FileNotFoundError:
        return False


def kill_script(script: str) -> None:
    subprocess.run(["pkill", "-f", f"/{script}$"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def start_script(script: str, extra_env: dict | None = None) -> None:
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    logf = f"/tmp/majel_{script.replace('.py', '')}.log"
    with open(logf, "ab") as log:
        p = subprocess.Popen(
            [str(ROOT / "venv" / "bin" / "python"), str(ROOT / script)],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL,
            start_new_session=True, env=env,
        )
    if script == "background.py":
        BG_PID.write_text(str(p.pid))


# ── Autostart helpers ────────────────────────────────────────────────────────
AUTOSTART_DIR = Path.home() / ".config" / "autostart"
AUTOSTART_FILE = AUTOSTART_DIR / "Majel-Control.desktop"
AUTOSTART_SOURCE = ROOT / "Majel-Control.desktop"


def autostart_enabled() -> bool:
    return AUTOSTART_FILE.exists()


def autostart_enable() -> bool:
    AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
    if not AUTOSTART_SOURCE.exists():
        return False
    body = AUTOSTART_SOURCE.read_text()
    if "X-GNOME-Autostart-enabled" not in body:
        if not body.endswith("\n"):
            body += "\n"
        body += "X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=4\n"
    AUTOSTART_FILE.write_text(body)
    try:
        AUTOSTART_FILE.chmod(0o755)
    except OSError:
        pass
    return True


def autostart_disable() -> bool:
    try:
        AUTOSTART_FILE.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def trekdata_clip_count() -> int:
    try:
        import sqlite3
        c = sqlite3.connect(str(TREK_DB))
        n = c.execute("SELECT COUNT(*) FROM clips").fetchone()[0]
        c.close()
        return n
    except Exception:
        return 0


def current_track_name() -> str:
    """Best-effort: read which mp3 ffplay is playing right now."""
    try:
        r = subprocess.run(["pgrep", "-af", "ffplay.*sounds/background"],
                           capture_output=True, text=True, timeout=1)
        for line in r.stdout.splitlines():
            m = re.search(r"sounds/background/([^\"' ]+\.mp3)", line)
            if m:
                stem = Path(m.group(1)).stem
                stem = re.sub(r"_\d+$", "", stem)
                return stem.replace("_", " ").upper()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return "—"


# ── LCARS canvas primitives ──────────────────────────────────────────────────
def draw_elbow_block(c: tk.Canvas, x: int, y: int, w: int, h: int,
                     color: str, *, round_corner: str,
                     radius: int = ELBOW_R, tag: str = "chrome") -> None:
    """Draw a colored rectangle with ONE rounded corner.

    `round_corner` ∈ {nw, ne, sw, se}. The rectangle fills (x, y, w, h) except
    for the cut-out at the named corner, which is rounded by `radius`. Anything
    OUTSIDE the corner curve simply shows the canvas background — no black
    overpaint, so layering bugs and stray rectangles cannot occur.
    """
    r = radius
    if round_corner == "nw":
        # Top-left rounded. Body fills (x+r..x+w, y..y+h) and (x..x+w, y+r..y+h).
        c.create_rectangle(x + r, y, x + w, y + h,
                           fill=color, outline=color, tags=tag)
        c.create_rectangle(x, y + r, x + r, y + h,
                           fill=color, outline=color, tags=tag)
        c.create_arc(x, y, x + 2*r, y + 2*r,
                     start=90, extent=90, fill=color, outline=color,
                     style="pieslice", tags=tag)
    elif round_corner == "sw":
        # Bottom-left rounded. Body fills the rect except the bottom-left arc.
        c.create_rectangle(x + r, y, x + w, y + h,
                           fill=color, outline=color, tags=tag)
        c.create_rectangle(x, y, x + r, y + h - r,
                           fill=color, outline=color, tags=tag)
        c.create_arc(x, y + h - 2*r, x + 2*r, y + h,
                     start=180, extent=90, fill=color, outline=color,
                     style="pieslice", tags=tag)
    elif round_corner == "ne":
        c.create_rectangle(x, y, x + w - r, y + h,
                           fill=color, outline=color, tags=tag)
        c.create_rectangle(x + w - r, y + r, x + w, y + h,
                           fill=color, outline=color, tags=tag)
        c.create_arc(x + w - 2*r, y, x + w, y + 2*r,
                     start=0, extent=90, fill=color, outline=color,
                     style="pieslice", tags=tag)
    elif round_corner == "se":
        c.create_rectangle(x, y, x + w - r, y + h,
                           fill=color, outline=color, tags=tag)
        c.create_rectangle(x + w - r, y, x + w, y + h - r,
                           fill=color, outline=color, tags=tag)
        c.create_arc(x + w - 2*r, y + h - 2*r, x + w, y + h,
                     start=270, extent=90, fill=color, outline=color,
                     style="pieslice", tags=tag)


# ── Pill button (canvas-drawn, used everywhere) ─────────────────────────────
def _brighten(hex_color: str, factor: float) -> str:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6:
        return hex_color
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r = max(0, min(255, int(r * factor)))
    g = max(0, min(255, int(g * factor)))
    b = max(0, min(255, int(b * factor)))
    return f"#{r:02x}{g:02x}{b:02x}"


class PillButton:
    def __init__(self, canvas: tk.Canvas, x: int, y: int, w: int, h: int,
                 text: str, command, *, color: str = LCARS["orange"],
                 hover: str | None = None, press: str | None = None,
                 fg: str = "#000000", round_side: str = "left",
                 font: tkfont.Font | None = None, tag_suffix: str = "",
                 clickable: bool = True):
        self.c = canvas
        self.cmd = command
        self.color = color
        self.hover = hover or _brighten(color, 1.15)
        self.press = press or _brighten(color, 0.7)
        self.tag = f"pill_{id(self)}_{tag_suffix}"
        r = h / 2
        if round_side == "left":
            self.arc = canvas.create_arc(x, y, x + h, y + h,
                                         start=90, extent=180,
                                         fill=color, outline=color,
                                         style="pieslice", tags=self.tag)
            self.rect = canvas.create_rectangle(x + r, y, x + w, y + h,
                                                fill=color, outline=color,
                                                tags=self.tag)
            tx = x + (w + r) / 2 + 4
        elif round_side == "right":
            self.rect = canvas.create_rectangle(x, y, x + w - r, y + h,
                                                fill=color, outline=color,
                                                tags=self.tag)
            self.arc = canvas.create_arc(x + w - h, y, x + w, y + h,
                                         start=270, extent=180,
                                         fill=color, outline=color,
                                         style="pieslice", tags=self.tag)
            tx = x + (w - r) / 2 - 4
        elif round_side == "none":
            # Square segment — flush with neighbours on both sides.
            self.rect = canvas.create_rectangle(x, y, x + w, y + h,
                                                fill=color, outline=color,
                                                tags=self.tag)
            tx = x + w / 2
        else:  # both
            self.arc = canvas.create_arc(x, y, x + h, y + h,
                                         start=90, extent=180,
                                         fill=color, outline=color,
                                         style="pieslice", tags=self.tag)
            self.rect = canvas.create_rectangle(x + r, y, x + w - r, y + h,
                                                fill=color, outline=color,
                                                tags=self.tag)
            self.arc2 = canvas.create_arc(x + w - h, y, x + w, y + h,
                                          start=270, extent=180,
                                          fill=color, outline=color,
                                          style="pieslice", tags=self.tag)
            tx = x + w / 2
        self.text_id = canvas.create_text(tx, y + h / 2 - 1,
                                          text=text.upper(), fill=fg,
                                          font=font or lcars_font(11, "bold"),
                                          tags=self.tag)
        if clickable:
            canvas.tag_bind(self.tag, "<Enter>", lambda e: self._set(self.hover))
            canvas.tag_bind(self.tag, "<Leave>", lambda e: self._set(self.color))
            canvas.tag_bind(self.tag, "<ButtonPress-1>", lambda e: self._set(self.press))
            canvas.tag_bind(self.tag, "<ButtonRelease-1>", self._on_release)

    def _set(self, c: str):
        for item in self.c.find_withtag(self.tag):
            kind = self.c.type(item)
            if kind in ("rectangle", "arc"):
                self.c.itemconfig(item, fill=c, outline=c)

    def _on_release(self, _):
        self._set(self.hover)
        if self.cmd:
            try:
                self.cmd()
            except Exception as ex:
                print(f"PillButton command error: {ex}")

    def set_text(self, text: str):
        self.c.itemconfig(self.text_id, text=text.upper())

    def set_color(self, color: str, fg: str | None = None):
        self.color = color
        self.hover = _brighten(color, 1.15)
        self.press = _brighten(color, 0.7)
        self._set(color)
        if fg is not None:
            self.c.itemconfig(self.text_id, fill=fg)


class _BarSegmentAdapter:
    """Minimal interface compatible with PillButton.set_text/set_color
    for the square middle segments in the bottom info bar."""
    def __init__(self, canvas: tk.Canvas, rect_id: int, text_id: int, *, color: str):
        self.c = canvas
        self.rect = rect_id
        self.text_id = text_id
        self.color = color

    def set_text(self, text: str):
        self.c.itemconfig(self.text_id, text=text.upper())

    def set_color(self, color: str, fg: str | None = None):
        self.color = color
        self.c.itemconfig(self.rect, fill=color, outline=color)
        if fg is not None:
            self.c.itemconfig(self.text_id, fill=fg)


# ── Main app ────────────────────────────────────────────────────────────────
class LCARSApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_cfg()

        root.title("LCARS 03-A • MAJEL CONTROL")
        root.geometry(f"{WIN_W}x{WIN_H}")
        root.resizable(True, True)
        root.minsize(MIN_W, MIN_H)
        root.configure(bg=LCARS["bg"])

        # Track current canvas dimensions (mutable; updated on resize).
        self.W = WIN_W
        self.H = WIN_H

        self.canvas = tk.Canvas(root, width=WIN_W, height=WIN_H,
                                bg=LCARS["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        # Debounced resize redraw of chrome (content stays in place).
        self._resize_after: str | None = None
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.f_title = lcars_font(20, "bold")
        self.f_rail_label = lcars_font(20, "bold")   # rail panel labels match title
        self.f_section = lcars_font(13, "bold")
        self.f_label = lcars_font(10, "bold")
        self.f_data = lcars_font(9, "bold")
        try:
            self.f_mono = tkfont.Font(family="DejaVu Sans Mono", size=10)
        except tk.TclError:
            self.f_mono = tkfont.Font(size=10)
        self._pills: dict[str, PillButton] = {}

        # `_section_layout` is populated by `_build_content` with the actual
        # y-ranges each content section occupies. `_draw_chrome` then draws
        # the rail panels using these ranges so the colored panels on the
        # left line up exactly with the section start/end on the right.
        self._section_layout: list[tuple[str, str, int, int]] = []

        # Build content first (records section bounds), THEN draw chrome
        # so the rail panels can size themselves to match.
        self._build_content()
        self._draw_chrome()

        self._refresh_status()
        self._refresh_bottom_bar()
        self._poll()

    # ── Chrome ────────────────────────────────────────────────────────────
    def _draw_chrome(self):
        """Render the LCARS chrome (elbows, rail, bar pills) at current W,H.

        Order matters: pill segments are drawn AFTER the elbow so they overlay
        directly onto the elbow's horizontal bar with no black gap. Rail panels
        are then drawn so they sit in front of the elbow's vertical rail.
        """
        c = self.canvas
        c.delete("chrome")
        # Bottom-bar info pills are part of the chrome and must be deleted on
        # resize too. Their tags are the per-pill suffixes; track them.
        for key in ("bot_clock", "bot_vmode", "bot_clips", "bot_svc"):
            for item in c.find_all():
                if any(t.endswith(key) for t in c.gettags(item)):
                    c.delete(item)

        W, H = self.W, self.H

        # ── Top elbow: a single bluey rectangle (RAIL_W × 96) with ONE
        # rounded corner at the top-left.
        draw_elbow_block(c, 0, 0, RAIL_W, 96, LCARS["bluey"],
                         round_corner="nw", radius=ELBOW_R)

        # ── Top bar: title segment + date segment, FLUSH against the elbow's
        # right edge. Both span the full BAR_H height (y=0..BAR_H). Title
        # text is centered both horizontally and vertically inside its segment.
        title_x = RAIL_W
        date_w = max(280, min(420, W // 4))
        title_w = W - title_x - date_w
        if title_w < 200:
            title_w = max(120, W - title_x - 120)
            date_w = W - title_x - title_w
        c.create_rectangle(title_x, 0, title_x + title_w, BAR_H,
                           fill=LCARS["bluey"], outline=LCARS["bluey"],
                           tags="chrome")
        c.create_text(title_x + title_w / 2, BAR_H / 2,
                      text="MAJEL CONTROL · LCARS 03-A",
                      fill=LCARS["bg"], anchor="center",
                      font=self.f_title, tags="chrome")
        date_x = title_x + title_w
        if date_w > 60:
            c.create_rectangle(date_x, 0, date_x + date_w, BAR_H,
                               fill=LCARS["orange"], outline=LCARS["orange"],
                               tags="chrome")
            self._date_text = c.create_text(date_x + date_w / 2, BAR_H / 2,
                                            text=self._stardate(),
                                            fill=LCARS["bg"], anchor="center",
                                            font=self.f_label, tags="chrome")

        # ── Bottom elbow: single orange rectangle (RAIL_W × 96) with ONE
        # rounded corner at the bottom-left. Matches the top-bar date pill.
        draw_elbow_block(c, 0, H - 96, RAIL_W, 96, LCARS["orange"],
                         round_corner="sw", radius=ELBOW_R)

        # ── Bottom bar: info segments flush, no margins, full bar height
        # (y=H-BAR_H..H). Same methodology as the top bar.
        bar_y = H - BAR_H
        live_pill_specs = [
            ("clock", LCARS["orange"],         "—"),
            ("vmode", LCARS["african_violet"], "MODE: —"),
            ("clips", LCARS["butterscotch"],   "CLIPS: 0"),
            ("svc",   LCARS["lima_bean"],      "SVC 0/4"),
        ]
        avail = W - RAIL_W
        # Allocate widths: 4 segments. Reserve fixed widths for clock/vmode/svc,
        # let clips absorb leftover width so segments span the entire bar.
        widths = [200, 240, 0, 200]
        widths[2] = max(140, avail - sum(widths))
        # If we still over/under-shoot due to small windows, shrink the largest.
        diff = sum(widths) - avail
        if diff != 0:
            widths[2] -= diff
            if widths[2] < 100:
                widths[2] = 100
                # absorb residual into vmode (index 1)
                widths[1] = max(140, avail - widths[0] - widths[2] - widths[3])
        x = RAIL_W
        for (key, color, init_text), w in zip(live_pill_specs, widths):
            # Last segment ends exactly at the right edge.
            if key == "svc":
                w = max(60, W - x)
            rect_id = c.create_rectangle(x, bar_y, x + w, H,
                                         fill=color, outline=color,
                                         tags=("chrome", f"bot_{key}"))
            txt_id = c.create_text(x + w - 12, H - 11, text=init_text,
                                   fill=LCARS["bg"], anchor="se",
                                   font=self.f_label,
                                   tags=("chrome", f"bot_{key}",
                                         f"bot_{key}_text"))
            self._pills[f"bot_{key}"] = _BarSegmentAdapter(
                c, rect_id, txt_id, color=color)
            x += w

        # ── Left rail panels — heights driven by `self._section_layout`,
        # which `_build_content` populated with the actual content y-ranges.
        # Panels TILE: each panel's bottom edge is the next panel's top edge,
        # so there are no black margins between them. The first panel starts
        # flush with the bottom of the top elbow; the last panel ends flush
        # with the top of the bottom elbow (so its color merges seamlessly
        # into the orange corner piece below).
        if self._section_layout:
            N = len(self._section_layout)
            # Build the split y-coordinates between adjacent panels.
            splits = [ELBOW_H]
            for i in range(1, N):
                splits.append(self._section_layout[i][2])
            splits.append(H - ELBOW_H)
            for i, (label, color, _ytop, _ybot) in enumerate(self._section_layout):
                py_top = splits[i]
                py_bot = splits[i + 1]
                if py_bot - py_top < 4:
                    continue
                c.create_rectangle(0, py_top, RAIL_W, py_bot,
                                   fill=color, outline=color, tags="chrome")
                # Centered label both horizontally and vertically.
                cx = RAIL_W / 2
                cy = (py_top + py_bot) / 2
                c.create_text(cx, cy, text=label,
                              fill=LCARS["bg"], font=self.f_rail_label,
                              anchor="center", tags="chrome")

    def _on_canvas_configure(self, event):
        # Debounce: schedule chrome redraw shortly after resize ends.
        new_w, new_h = max(MIN_W, event.width), max(MIN_H, event.height)
        if new_w == self.W and new_h == self.H:
            return
        self.W, self.H = new_w, new_h
        if self._resize_after:
            self.canvas.after_cancel(self._resize_after)
        self._resize_after = self.canvas.after(50, self._draw_chrome)

    def _stardate(self) -> str:
        """Return a combined date + TNG-style stardate string.

        Stardate formula (popular fan formula): 1000 units = 1 calendar year,
        anchored so 1946 ≈ stardate 0. For May 1 2026 this yields ~80330.
        """
        now = _dt.datetime.now()
        days_in_year = 366 if (now.year % 4 == 0 and (now.year % 100 != 0
                                                       or now.year % 400 == 0)) else 365
        frac = (now.timetuple().tm_yday - 1
                + now.hour / 24
                + now.minute / 1440) / days_in_year
        stardate = (now.year - 1946 + frac) * 1000
        date_str = now.strftime("%b %d %Y").upper()
        return f"STARDATE {stardate:.1f} · {date_str}"

    # ── Content ───────────────────────────────────────────────────────────
    def _build_content(self):
        c = self.canvas
        x0 = CONTENT_X
        y0 = CONTENT_Y
        col_w = WIN_W - x0 - 16

        # ── VOICE OPERATIONS ────────────────────────────────────────────
        y = y0
        sec_voice_top = y
        c.create_text(x0, y, anchor="nw", text="VOICE OPERATIONS",
                      fill=LCARS["sunflower"], font=self.f_section)
        y += 22

        # Voice ops row: three pills flush together, spanning full content width.
        self.voice_var = tk.BooleanVar(value=self.cfg["voice_enabled"])
        self.bg_enabled_var = tk.BooleanVar(value=self.cfg["bg_enabled"])
        self.enter_var = tk.BooleanVar(value=self.cfg.get("enter_sound", True))

        third = col_w // 3
        v_w = third
        m_w = col_w - 2 * third
        e_w = third
        self._pills["voice_toggle"] = PillButton(
            c, x0, y, v_w, 38,
            "VOICE: ON" if self.voice_var.get() else "VOICE: OFF",
            self._on_voice_toggle,
            color=LCARS["african_violet"] if self.voice_var.get() else LCARS["red"],
            round_side="left", font=self.f_label, tag_suffix="voice_toggle",
        )
        self._pills["bg_toggle"] = PillButton(
            c, x0 + v_w, y, m_w, 38,
            "MUSIC: ON" if self.bg_enabled_var.get() else "MUSIC: OFF",
            self._on_bg_enable_toggle,
            color=LCARS["bluey"] if self.bg_enabled_var.get() else LCARS["red"],
            round_side="none", font=self.f_label, tag_suffix="bg_toggle",
        )
        self._pills["enter_toggle"] = PillButton(
            c, x0 + v_w + m_w, y, e_w, 38,
            "ENTER SFX: ON" if self.enter_var.get() else "ENTER SFX: OFF",
            self._on_enter_toggle,
            color=LCARS["lima_bean"] if self.enter_var.get() else LCARS["red"],
            round_side="right", font=self.f_label, tag_suffix="enter_toggle",
        )
        y += 56
        sec_voice_bot = y
        self._section_layout.append(
            ("VOICE", LCARS["african_violet"], sec_voice_top, sec_voice_bot))
        y += 8

        # ── VOICE MODE ───────────────────────────────────────────────────
        sec_mode_top = y
        c.create_text(x0, y, anchor="nw", text="VOICE MODE",
                      fill=LCARS["sunflower"], font=self.f_section)
        y += 22
        self.mode_var = tk.StringVar(value=self.cfg.get("voice_mode", "offline"))
        half = col_w // 2
        self._mode_btn_pos = {
            "offline": (x0,         y, LCARS["bluey"], half),
            "api":     (x0 + half,  y, LCARS["orange"], col_w - half),
        }
        self._draw_mode_pills()
        y += 50 + 4

        # API key entry inside a transparent frame
        c.create_text(x0, y + 9, anchor="nw", text="API KEY",
                      fill=LCARS["african_violet"], font=self.f_label)
        key_frame = tk.Frame(c, bg=LCARS["bg"])
        self.api_key_var = tk.StringVar(value=self.cfg.get("anthropic_api_key", ""))
        self.api_entry = tk.Entry(
            key_frame, textvariable=self.api_key_var, show="•",
            bg="#1a1a26", fg=LCARS["space_white"],
            insertbackground=LCARS["sunflower"],
            relief="flat", bd=0, font=self.f_mono,
            highlightthickness=2, highlightbackground=LCARS["african_violet"],
            highlightcolor=LCARS["orange"],
        )
        self.api_entry.pack(side="left", fill="both", expand=True, ipady=6)
        c.create_window(x0 + 80, y + 4, anchor="nw", window=key_frame,
                        width=460, height=34)

        self._pills["api_save"] = PillButton(
            c, x0 + 548, y + 4, 80, 32, "SAVE", self._save_api_key,
            color=LCARS["orange"], round_side="left", font=self.f_label,
            tag_suffix="api_save",
        )
        self._pills["api_test"] = PillButton(
            c, x0 + 632, y + 4, 80, 32, "TEST", self._test_api_key,
            color=LCARS["african_violet"], round_side="right",
            font=self.f_label, tag_suffix="api_test",
        )
        y += 44
        self.api_status_id = c.create_text(
            x0, y, anchor="nw",
            text="STATUS: " + self._mode_summary().upper(),
            fill=LCARS["space_white"], font=self.f_label,
        )
        y += 28
        sec_mode_bot = y
        self._section_layout.append(
            ("MODE", LCARS["lilac"], sec_mode_top, sec_mode_bot))
        y += 8

        # ── AUDIO LEVELS ─────────────────────────────────────────────────
        sec_audio_top = y
        c.create_text(x0, y, anchor="nw", text="AUDIO LEVELS",
                      fill=LCARS["sunflower"], font=self.f_section)
        y += 22

        s = ttk.Style()
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure("LCARS.Horizontal.TScale",
                    background=LCARS["bg"],
                    troughcolor="#221122",
                    borderwidth=0)

        self.vol_var = tk.IntVar(value=self.cfg["bg_volume"])
        c.create_text(x0, y + 8, anchor="nw", text="BACKGROUND",
                      fill=LCARS["african_violet"], font=self.f_label)
        sf1 = tk.Frame(c, bg=LCARS["bg"])
        # Slider runs 0–200 so the user can boost the music sink above the
        # default mixer ceiling — PulseAudio honors >100% via software gain.
        ttk.Scale(sf1, from_=0, to=200, variable=self.vol_var,
                  style="LCARS.Horizontal.TScale",
                  command=lambda _: self._on_vol_change()).pack(fill="x")
        c.create_window(x0 + 134, y + 4, anchor="nw", window=sf1, width=460, height=28)
        self._vol_readout = c.create_text(x0 + 600, y + 8, anchor="nw",
                                          text=f"{self.vol_var.get():3d}%",
                                          fill=LCARS["space_white"],
                                          font=self.f_label)
        y += 30

        # DUCK = the percentage of background volume that gets CUT while
        # voice plays. 100% = music fully silenced during voice; 0% = no
        # ducking. The value stored on disk (`duck_volume`) remains the
        # *floor* (= 100 - cut) for back-compat with background.py, but
        # the slider and readout speak in cut-amount terms because that
        # matches user intuition ("how much do I want to cut").
        floor = int(self.cfg.get("duck_volume", 6))
        cut = max(0, min(100, 100 - floor))
        self.duck_var = tk.IntVar(value=cut)
        c.create_text(x0, y + 8, anchor="nw", text="DUCK",
                      fill=LCARS["african_violet"], font=self.f_label)
        sf2 = tk.Frame(c, bg=LCARS["bg"])
        ttk.Scale(sf2, from_=0, to=100, variable=self.duck_var,
                  style="LCARS.Horizontal.TScale",
                  command=lambda _: self._on_duck_change()).pack(fill="x")
        c.create_window(x0 + 134, y + 4, anchor="nw", window=sf2, width=460, height=28)
        self._duck_readout = c.create_text(x0 + 600, y + 8, anchor="nw",
                                           text=f"{self.duck_var.get():3d}%",
                                           fill=LCARS["space_white"],
                                           font=self.f_label)
        y += 30
        sec_audio_bot = y
        self._section_layout.append(
            ("AUDIO", LCARS["butterscotch"], sec_audio_top, sec_audio_bot))
        y += 8

        # ── MUSIC PROGRAM ────────────────────────────────────────────────
        sec_music_top = y
        c.create_text(x0, y, anchor="nw", text="MUSIC PROGRAM",
                      fill=LCARS["sunflower"], font=self.f_section)
        y += 22

        # Group cycler row: PREV / CURRENT / NEXT, fills full content width,
        # with the middle (current track display) square on both ends.
        self._music_groups = ["(random)"] + list_bg_groups()
        cur = self.cfg["bg_group"] or "(random)"
        if cur not in self._music_groups:
            cur = "(random)"
        self._music_idx = self._music_groups.index(cur)

        nav_w = 70
        cur_w = col_w - 2 * nav_w
        self._pills["music_prev"] = PillButton(
            c, x0, y, nav_w, 36, "◀", self._music_prev,
            color=LCARS["lilac"], round_side="left", font=self.f_label,
            tag_suffix="music_prev",
        )
        self._pills["music_current"] = PillButton(
            c, x0 + nav_w, y, cur_w, 36,
            self._music_label_text(), command=None,
            color=LCARS["butterscotch"], round_side="none",
            font=self.f_label, tag_suffix="music_current", clickable=False,
        )
        self._pills["music_next"] = PillButton(
            c, x0 + nav_w + cur_w, y, nav_w, 36, "▶", self._music_next,
            color=LCARS["lilac"], round_side="right", font=self.f_label,
            tag_suffix="music_next",
        )
        y += 50

        # Three playback-mode pills: sequence / loop / random. Selecting one
        # restarts background.py with the corresponding MAJEL_BG_MODE env var
        # so the daemon honors the mode. The currently-active mode is shown
        # as a fully-saturated pill; inactive modes are dimmed.
        cur_mode = self.cfg.get("bg_mode", "loop")
        self._mode_pill_specs = [
            ("sequence", "PLAY IN SEQUENCE", LCARS["bluey"],          "left"),
            ("loop",     "LOOP",             LCARS["butterscotch"],   "none"),
            ("random",   "RANDOM SEQUENCE",  LCARS["african_violet"], "right"),
        ]
        # Allocate widths to fill col_w. LOOP gets a fixed 140px; the two
        # outer pills split the remainder evenly.
        loop_w = 140
        side_w = (col_w - loop_w) // 2
        widths = [side_w, col_w - 2 * side_w, side_w]
        # Round so pill 3 ends exactly at x0 + col_w.
        if widths[0] + widths[1] + widths[2] != col_w:
            widths[1] = col_w - widths[0] - widths[2]
        self._bg_mode_geom: list[tuple[int, int, int]] = []
        x = x0
        for (mode, label, color, side), w in zip(self._mode_pill_specs, widths):
            self._bg_mode_geom.append((x, y, w))
            active = (cur_mode == mode)
            if side == "none":
                # Square segment (visual middle) — flush, no rounding. Tag
                # is content-only ("bg_mode_seg"), NOT "chrome", because
                # _draw_chrome() deletes everything tagged "chrome" on each
                # redraw and was wiping out the LOOP pill.
                rect_id = c.create_rectangle(
                    x, y, x + w, y + 36,
                    fill=color if active else "#1d1d2a",
                    outline=color if active else "#1d1d2a",
                    tags=("bg_mode_seg", f"pill_bg_{mode}"))
                txt_id = c.create_text(
                    x + w / 2, y + 18, text=label,
                    fill=LCARS["bg"] if active else color,
                    font=self.f_label,
                    tags=("bg_mode_seg", f"pill_bg_{mode}"))
                # Bind click via tag.
                c.tag_bind(f"pill_bg_{mode}", "<ButtonRelease-1>",
                           lambda e, m=mode: self._set_bg_mode(m))
                self._pills[f"bg_mode_{mode}"] = _BarSegmentAdapter(
                    c, rect_id, txt_id, color=color)
            else:
                self._pills[f"bg_mode_{mode}"] = PillButton(
                    c, x, y, w, 36, label,
                    lambda m=mode: self._set_bg_mode(m),
                    color=color if active else "#1d1d2a",
                    hover=color if active else _brighten(color, 0.55),
                    press=color,
                    fg=LCARS["bg"] if active else color,
                    round_side=side, font=self.f_label,
                    tag_suffix=f"bg_mode_{mode}",
                )
            x += w
        y += 50

        # Currently-playing readout
        c.create_text(x0, y, anchor="nw", text="NOW PLAYING:",
                      fill=LCARS["african_violet"], font=self.f_label)
        self._now_playing = c.create_text(x0 + 110, y, anchor="nw", text="—",
                                          fill=LCARS["space_white"],
                                          font=self.f_label)
        y += 26
        sec_music_bot = y
        self._section_layout.append(
            ("MUSIC", LCARS["bluey"], sec_music_top, sec_music_bot))
        y += 10

        # ── BRIEFING ─────────────────────────────────────────────────────
        # Trigger a Majel-voice project briefing. Pick a project (defaults
        # to this one) and the depth — FULL / RECENT / MOMENTUM. The
        # briefing is generated by scripts/majel_briefing.py and queued
        # through the standard speak.py path.
        sec_brief_top = y
        c.create_text(x0, y, anchor="nw", text="BRIEFING",
                      fill=LCARS["sunflower"], font=self.f_section)
        y += 22

        # State init.
        if not hasattr(self, "_briefing_project"):
            self._briefing_project = ROOT
        if not hasattr(self, "_briefing_mode"):
            self._briefing_mode = "full"
        # In-memory caches keyed by (project_path, mode). Avoid re-reading
        # files / re-calling the LLM when the captain triggers the same
        # briefing twice in a session.
        if not hasattr(self, "_briefing_ctx_cache"):
            self._briefing_ctx_cache: dict[tuple[str, str], str] = {}
        if not hasattr(self, "_briefing_out_cache"):
            self._briefing_out_cache: dict[tuple[str, str], str] = {}

        # Project selector row: PROJECT label · main project pill (dropdown
        # of remembered Claude projects) · small folder pill (browse any).
        c.create_text(x0, y + 8, anchor="nw", text="PROJECT",
                      fill=LCARS["african_violet"], font=self.f_label)
        proj_label = (self._briefing_project.name or str(self._briefing_project)) + "  ▾"
        folder_w = 44
        proj_x = x0 + 110
        proj_w = col_w - 110 - folder_w - 8
        self._pills["briefing_project"] = PillButton(
            c, proj_x, y, proj_w, 32, proj_label,
            self._pick_briefing_project,
            color=LCARS["bluey"], round_side="left", font=self.f_label,
            tag_suffix="briefing_project",
        )
        self._pills["briefing_browse"] = PillButton(
            c, proj_x + proj_w + 8, y, folder_w, 32, "📁",
            self._browse_briefing_project,
            color=LCARS["lilac"], round_side="right", font=self.f_label,
            tag_suffix="briefing_browse",
        )
        y += 42

        # Two mode pills: FULL BRIEF / QUICK BRIEF. Active is bright.
        mode_specs = [
            ("full",  "FULL BRIEF",  LCARS["butterscotch"],   "left"),
            ("quick", "QUICK BRIEF", LCARS["african_violet"], "right"),
        ]
        mode_w = col_w // 2
        widths = [mode_w, col_w - mode_w]
        x = x0
        for (mode, label, color, side), w in zip(mode_specs, widths):
            active = (self._briefing_mode == mode)
            self._pills[f"brief_mode_{mode}"] = PillButton(
                c, x, y, w, 36, label,
                lambda m=mode: self._set_briefing_mode(m),
                color=color if active else "#1d1d2a",
                hover=color if active else _brighten(color, 0.55),
                press=color,
                fg=LCARS["bg"] if active else color,
                round_side=side, font=self.f_label,
                tag_suffix=f"brief_mode_{mode}",
            )
            x += w
        y += 50

        # Trigger pill spans the full width.
        self._pills["briefing_trigger"] = PillButton(
            c, x0, y, col_w, 40, "▶  TRIGGER BRIEFING",
            self._trigger_briefing,
            color=LCARS["lima_bean"], round_side="both", font=self.f_section,
            tag_suffix="briefing_trigger",
        )
        y += 50

        sec_brief_bot = y
        self._section_layout.append(
            ("BRIEFING", LCARS["sunflower"], sec_brief_top, sec_brief_bot))
        y += 10

        # ── NARRATION ────────────────────────────────────────────────────
        # When enabled, the PostToolUse hook (step_hook.sh) calls Majel
        # to narrate substantive tool calls (Edit/Write/Bash-with-desc)
        # in real-time, with a 25s throttle so it doesn't flood. Off by
        # default — opt in by toggling the pill below.
        sec_narr_top = y
        c.create_text(x0, y, anchor="nw", text="NARRATION",
                      fill=LCARS["sunflower"], font=self.f_section)
        y += 26
        c.create_text(x0, y + 6, anchor="nw",
                      text="STEP-BY-STEP",
                      fill=LCARS["african_violet"], font=self.f_label)
        narrate_on = bool(self.cfg.get("narrate_during_build", False))
        self._pills["narrate_toggle"] = PillButton(
            c, x0 + 160, y, col_w - 160, 36,
            "ENABLED  ●" if narrate_on else "DISABLED  ○",
            self._toggle_narrate,
            color=LCARS["lima_bean"] if narrate_on else "#1d1d2a",
            hover=LCARS["lima_bean"] if narrate_on else _brighten(LCARS["lima_bean"], 0.55),
            press=LCARS["lima_bean"],
            fg=LCARS["bg"] if narrate_on else LCARS["lima_bean"],
            round_side="both", font=self.f_label,
            tag_suffix="narrate_toggle",
        )
        y += 46
        # Stuck-loop detector — fires "Caution. <file> loop detected.
        # N iterations. No progress detected." when the agent has been
        # spinning. Auto-launched by session_start.sh so flipping this
        # pill takes effect live without restarting anything.
        c.create_text(x0, y + 6, anchor="nw",
                      text="STUCK-LOOP",
                      fill=LCARS["african_violet"], font=self.f_label)
        loop_on = bool(self.cfg.get("loop_watcher_enabled", False))
        self._pills["loop_toggle"] = PillButton(
            c, x0 + 160, y, col_w - 160, 36,
            "ENABLED  ●" if loop_on else "DISABLED  ○",
            self._toggle_loop_watcher,
            color=LCARS["lima_bean"] if loop_on else "#1d1d2a",
            hover=LCARS["lima_bean"] if loop_on else _brighten(LCARS["lima_bean"], 0.55),
            press=LCARS["lima_bean"],
            fg=LCARS["bg"] if loop_on else LCARS["lima_bean"],
            round_side="both", font=self.f_label,
            tag_suffix="loop_toggle",
        )
        y += 46
        c.create_text(x0, y, anchor="nw",
                      text="Step hook: wire step_hook.sh as PostToolUse",
                      fill=LCARS["space_white"], font=self.f_mono)
        y += 14
        c.create_text(x0, y, anchor="nw",
                      text="in ~/.claude/settings.json. Loop detector: auto.",
                      fill=LCARS["space_white"], font=self.f_mono)
        y += 22
        sec_narr_bot = y
        self._section_layout.append(
            ("NARRATION", LCARS["lima_bean"], sec_narr_top, sec_narr_bot))
        y += 18

        # ── SUBSYSTEMS (with status pills) ──────────────────────────────
        sec_sys_top = y
        c.create_text(x0, y, anchor="nw", text="SUBSYSTEMS",
                      fill=LCARS["sunflower"], font=self.f_section)
        y += 22

        self._svc_status_pills: dict[str, PillButton] = {}
        ROW_H = 30
        for i, (script, label) in enumerate(DAEMONS.items()):
            ry = y + i * ROW_H
            c.create_text(x0, ry + 8, anchor="nw", text=label,
                          fill=LCARS["space_white"], font=self.f_label)
            self._svc_status_pills[script] = PillButton(
                c, x0 + 220, ry + 2, 120, 24, "—", command=None,
                color="#444444", round_side="left", font=self.f_label,
                tag_suffix=f"svc_{script}", clickable=False,
            )
            self._pills[f"svc_restart_{script}"] = PillButton(
                c, x0 + 348, ry + 2, 110, 24, "RESTART",
                lambda s=script: self._restart(s),
                color=LCARS["african_violet"], round_side="right",
                font=self.f_label, tag_suffix=f"svc_restart_{script}",
            )
        y += len(DAEMONS) * ROW_H + 4
        sec_sys_bot = y
        self._section_layout.append(
            ("SUBSYS", LCARS["sky"], sec_sys_top, sec_sys_bot))
        y += 8

        # ── STARTUP (autostart toggle) ──────────────────────────────────
        sec_startup_top = y
        c.create_text(x0, y, anchor="nw", text="STARTUP",
                      fill=LCARS["sunflower"], font=self.f_section)
        y += 32

        on_now = autostart_enabled()
        self._pills["autostart_toggle"] = PillButton(
            c, x0, y, col_w, 50,
            "AUTOSTART: ON" if on_now else "AUTOSTART: OFF",
            self._on_autostart_toggle,
            color=LCARS["orange"] if on_now else LCARS["red"],
            round_side="both", font=self.f_label,
            tag_suffix="autostart_toggle",
        )
        y += 66
        sec_startup_bot = y
        self._section_layout.append(
            ("STARTUP", LCARS["orange"], sec_startup_top, sec_startup_bot))

    def _on_autostart_toggle(self):
        if autostart_enabled():
            autostart_disable()
            self._pills["autostart_toggle"].set_text("AUTOSTART: OFF")
            self._pills["autostart_toggle"].set_color(LCARS["red"], fg=LCARS["bg"])
        else:
            autostart_enable()
            self._pills["autostart_toggle"].set_text("AUTOSTART: ON")
            self._pills["autostart_toggle"].set_color(LCARS["orange"], fg=LCARS["bg"])

    def _music_label_text(self) -> str:
        if not self._music_groups:
            return "—"
        name = self._music_groups[self._music_idx]
        return name.replace("_", " ").upper()

    def _music_prev(self):
        if not self._music_groups:
            return
        self._music_idx = (self._music_idx - 1) % len(self._music_groups)
        self._pills["music_current"].set_text(self._music_label_text())
        self._apply_music_change()

    def _music_next(self):
        if not self._music_groups:
            return
        self._music_idx = (self._music_idx + 1) % len(self._music_groups)
        self._pills["music_current"].set_text(self._music_label_text())
        self._apply_music_change()

    def _apply_music_change(self):
        """Persist the selected group and restart background.py so the new
        track plays immediately. ◀/▶ no longer require a separate
        play-mode press."""
        cur = self._music_groups[self._music_idx]
        self.cfg["bg_group"] = "" if cur == "(random)" else cur
        save_cfg(self.cfg)
        self._restart("background.py")

    # ── Voice mode helpers ────────────────────────────────────────────────
    def _mode_summary(self) -> str:
        mode = self.cfg.get("voice_mode", "offline")
        has_key = bool(self.cfg.get("anthropic_api_key"))
        if mode == "api":
            return "API mode active" if has_key else "API mode selected — key required"
        return "Offline mode (regex+templates)"

    def _draw_mode_pills(self):
        c = self.canvas
        # Delete prior mode pills
        for suf in ("mode_offline", "mode_api"):
            for item in c.find_all():
                if any(t.endswith(suf) for t in c.gettags(item)):
                    c.delete(item)
        active = self.mode_var.get()
        for label, mode, side in [
            ("OFFLINE • TEMPLATES", "offline", "left"),
            ("CLAUDE API • CONTEXT", "api", "right"),
        ]:
            geom = self._mode_btn_pos[mode]
            # geom may be (x, y, color) (legacy) or (x, y, color, w) (new).
            if len(geom) == 4:
                x, y, color, w = geom
            else:
                x, y, color = geom
                w = 360
            is_active = (active == mode)
            self._pills[f"mode_{mode}"] = PillButton(
                c, x, y, w, 38, label,
                lambda m=mode: self._set_mode(m),
                color=color if is_active else "#1d1d2a",
                hover=color if is_active else _brighten(color, 0.45),
                press=color,
                fg=LCARS["bg"] if is_active else color,
                round_side=side, font=self.f_label,
                tag_suffix=f"mode_{mode}",
            )

    def _set_mode(self, mode: str):
        self.mode_var.set(mode)
        self.cfg["voice_mode"] = mode
        save_cfg(self.cfg)
        self._draw_mode_pills()
        self.canvas.itemconfig(self.api_status_id,
                               text="STATUS: " + self._mode_summary().upper())

    # ── Toggle handlers ───────────────────────────────────────────────────
    def _on_voice_toggle(self):
        new = not self.voice_var.get()
        self.voice_var.set(new)
        self.cfg["voice_enabled"] = new
        save_cfg(self.cfg)
        self._pills["voice_toggle"].set_text("VOICE: ON" if new else "VOICE: OFF")
        self._pills["voice_toggle"].set_color(
            LCARS["african_violet"] if new else LCARS["red"])

    def _on_bg_enable_toggle(self):
        new = not self.bg_enabled_var.get()
        self.bg_enabled_var.set(new)
        self.cfg["bg_enabled"] = new
        save_cfg(self.cfg)
        self._pills["bg_toggle"].set_text("MUSIC: ON" if new else "MUSIC: OFF")
        self._pills["bg_toggle"].set_color(
            LCARS["bluey"] if new else LCARS["red"])
        if new:
            if not is_running("background.py"):
                env = {"MAJEL_BG_GROUP": self.cfg["bg_group"]} if self.cfg["bg_group"] else None
                start_script("background.py", extra_env=env)
        else:
            kill_script("background.py")

    def _on_enter_toggle(self):
        new = not self.enter_var.get()
        self.enter_var.set(new)
        self.cfg["enter_sound"] = new
        save_cfg(self.cfg)
        self._pills["enter_toggle"].set_text(
            "ENTER SFX: ON" if new else "ENTER SFX: OFF")
        self._pills["enter_toggle"].set_color(
            LCARS["lima_bean"] if new else LCARS["red"])

    # ── API helpers ───────────────────────────────────────────────────────
    def _save_api_key(self):
        self.cfg["anthropic_api_key"] = self.api_key_var.get().strip()
        save_cfg(self.cfg)
        self.canvas.itemconfig(
            self.api_status_id,
            text="STATUS: SAVED — " + self._mode_summary().upper(),
        )

    def _test_api_key(self):
        key = self.api_key_var.get().strip()
        if not key:
            self.canvas.itemconfig(self.api_status_id, text="STATUS: NO KEY ENTERED")
            return
        self.canvas.itemconfig(self.api_status_id, text="STATUS: TESTING…")
        self.root.update_idletasks()

        def worker():
            try:
                from anthropic import Anthropic
                client = Anthropic(api_key=key)
                r = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=20,
                    messages=[{"role": "user", "content": "Say only the word: nominal"}],
                )
                txt = "".join(b.text for b in r.content
                              if getattr(b, "type", None) == "text").strip()
                ok = "nominal" in txt.lower()
                msg = ("STATUS: KEY VALID — " + self._mode_summary().upper()) if ok \
                      else f"STATUS: UNEXPECTED REPLY: {txt[:32].upper()}"
            except Exception as ex:
                msg = f"STATUS: FAILED — {type(ex).__name__.upper()}"
            self.root.after(0, lambda: self.canvas.itemconfig(self.api_status_id, text=msg))

        threading.Thread(target=worker, daemon=True).start()

    # ── Music helpers ─────────────────────────────────────────────────────
    def _apply_bg_group(self):
        sel = self._music_groups[self._music_idx] if self._music_groups else "(random)"
        self.cfg["bg_group"] = "" if sel == "(random)" else sel
        save_cfg(self.cfg)
        self._restart_background()

    def _skip_track(self):
        subprocess.run(["pkill", "-f", "ffplay.*sounds/background"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _restart_background(self):
        """Restart background.py with current group + mode env vars."""
        kill_script("background.py")
        time.sleep(0.3)
        env = {"MAJEL_BG_MODE": self.cfg.get("bg_mode", "sequence")}
        if self.cfg.get("bg_group"):
            env["MAJEL_BG_GROUP"] = self.cfg["bg_group"]
        start_script("background.py", extra_env=env)

    def _set_bg_mode(self, mode: str):
        """Switch playback mode to sequence / loop / random and restart."""
        if self.cfg.get("bg_mode") == mode:
            # Toggle off → already on; do nothing (mode is sticky).
            pass
        self.cfg["bg_mode"] = mode
        save_cfg(self.cfg)
        # Apply the music group selection at the same time so the user gets
        # one-click "play this group in this mode" behaviour.
        if self._music_groups:
            sel = self._music_groups[self._music_idx]
            self.cfg["bg_group"] = "" if sel == "(random)" else sel
            save_cfg(self.cfg)
        self._restart_background()
        # Repaint the three mode pills so the active one fills with color.
        self._redraw_bg_mode_pills()

    def _redraw_bg_mode_pills(self):
        c = self.canvas
        for mode in ("sequence", "loop", "random"):
            for item in c.find_all():
                if any(t.endswith(f"bg_mode_{mode}") or t == f"pill_bg_{mode}"
                       for t in c.gettags(item)):
                    c.delete(item)
        # Replay the same construction logic from _build_content. To avoid
        # duplicating it, store positions on first build.
        if not hasattr(self, "_bg_mode_geom"):
            return
        cur = self.cfg.get("bg_mode", "sequence")
        for (mode, label, color, side), (x, y, w) in zip(
                self._mode_pill_specs, self._bg_mode_geom):
            active = (cur == mode)
            if side == "none":
                rect_id = c.create_rectangle(
                    x, y, x + w, y + 36,
                    fill=color if active else "#1d1d2a",
                    outline=color if active else "#1d1d2a",
                    tags=("chrome", f"pill_bg_{mode}"))
                txt_id = c.create_text(
                    x + w / 2, y + 18, text=label,
                    fill=LCARS["bg"] if active else color,
                    font=self.f_label,
                    tags=("chrome", f"pill_bg_{mode}"))
                c.tag_bind(f"pill_bg_{mode}", "<ButtonRelease-1>",
                           lambda e, m=mode: self._set_bg_mode(m))
                self._pills[f"bg_mode_{mode}"] = _BarSegmentAdapter(
                    c, rect_id, txt_id, color=color)
            else:
                self._pills[f"bg_mode_{mode}"] = PillButton(
                    c, x, y, w, 36, label,
                    lambda m=mode: self._set_bg_mode(m),
                    color=color if active else "#1d1d2a",
                    hover=color if active else _brighten(color, 0.55),
                    press=color,
                    fg=LCARS["bg"] if active else color,
                    round_side=side, font=self.f_label,
                    tag_suffix=f"bg_mode_{mode}",
                )

    # ── Volume sliders ────────────────────────────────────────────────────
    def _on_vol_change(self):
        v = int(self.vol_var.get())
        self.cfg["bg_volume"] = v
        set_bg_volume(v)
        self.canvas.itemconfig(self._vol_readout, text=f"{v:3d}%")

    def _on_duck_change(self):
        cut = int(self.duck_var.get())
        # Store the floor (residual) on disk, not the cut, so background.py's
        # existing _set_volume(DUCK_VOLUME) call still receives a valid
        # "play at X%" number.
        floor = max(0, min(100, 100 - cut))
        self.cfg["duck_volume"] = floor
        self.canvas.itemconfig(self._duck_readout, text=f"{cut:3d}%")

    # ── Loop-watcher toggle ──────────────────────────────────────────────
    def _toggle_loop_watcher(self):
        new_state = not bool(self.cfg.get("loop_watcher_enabled", False))
        self.cfg["loop_watcher_enabled"] = new_state
        save_cfg(self.cfg)
        pill = self._pills.get("loop_toggle")
        if not pill:
            return
        if new_state:
            pill.set_text("ENABLED  ●")
            pill.set_color(LCARS["lima_bean"], fg=LCARS["bg"])
        else:
            pill.set_text("DISABLED  ○")
            pill.set_color("#1d1d2a", fg=LCARS["lima_bean"])

    # ── Narration toggle ─────────────────────────────────────────────────
    def _toggle_narrate(self):
        new_state = not bool(self.cfg.get("narrate_during_build", False))
        self.cfg["narrate_during_build"] = new_state
        save_cfg(self.cfg)
        pill = self._pills.get("narrate_toggle")
        if not pill:
            return
        if new_state:
            pill.set_text("ENABLED  ●")
            pill.set_color(LCARS["lima_bean"], fg=LCARS["bg"])
        else:
            pill.set_text("DISABLED  ○")
            pill.set_color("#1d1d2a", fg=LCARS["lima_bean"])

    # ── Briefing handlers ────────────────────────────────────────────────
    def _claude_project_paths(self) -> list[Path]:
        """Decode every ~/.claude/projects/<slug>/ into the real filesystem
        path. Slug format: leading '-' followed by path components joined
        with '-'. Only return entries whose decoded path actually exists
        as a directory (so deleted projects don't clutter the menu)."""
        out: list[Path] = []
        base = Path.home() / ".claude" / "projects"
        if not base.is_dir():
            return out
        for d in base.iterdir():
            if not d.is_dir():
                continue
            slug = d.name.lstrip("-")
            if not slug:
                continue
            real = Path("/" + slug.replace("-", "/"))
            if real.is_dir():
                out.append(real)
        # Sort by mtime of the slug dir (most recent first) so the active
        # projects float to the top of the dropdown.
        out.sort(key=lambda p: -((Path.home() / ".claude" / "projects" /
                                  ("-" + str(p).lstrip("/").replace("/", "-"))).stat().st_mtime
                                 if (Path.home() / ".claude" / "projects" /
                                     ("-" + str(p).lstrip("/").replace("/", "-"))).exists()
                                 else 0))
        return out

    def _pick_briefing_project(self):
        """Open the Claude-project dropdown next to the PROJECT pill.

        Always lists the currently-selected project at the top (with a
        ✓ marker) so the user can confirm what they're about to brief
        without having to dismiss + reopen. Clicking off the menu
        auto-dismisses via tk.Menu's built-in grab semantics — DO NOT
        call grab_release() ourselves immediately after tk_popup, that
        prematurely tears down the global grab and leaves the menu
        stuck open until the next click."""
        menu = tk.Menu(self.root, tearoff=0,
                       bg="#1a1a26", fg=LCARS["space_white"],
                       activebackground=LCARS["bluey"],
                       activeforeground=LCARS["bg"],
                       borderwidth=0)
        projects = self._claude_project_paths()
        current = self._briefing_project

        # Pin the current project at the top so the user can see what's
        # selected and re-confirm with one click. Other projects below.
        menu.add_command(
            label=f"✓  {current.name}  (current)",
            command=lambda p=current: self._set_briefing_project(p),
        )
        # Filter dupes from the remembered list.
        others = [p for p in projects if p.resolve() != current.resolve()]
        if others:
            menu.add_separator()
            for p in others[:20]:
                menu.add_command(
                    label=f"{p.name}  —  {p}",
                    command=lambda pp=p: self._set_briefing_project(pp),
                )
        menu.add_separator()
        menu.add_command(label="📁  Browse for any folder…",
                         command=self._browse_briefing_project)
        x, y = self.root.winfo_pointerxy()
        menu.tk_popup(x, y)

    def _browse_briefing_project(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(
            title="Select any folder to brief",
            initialdir=str(self._briefing_project.parent
                           if self._briefing_project.parent.is_dir() else Path.home()),
        )
        if path:
            self._set_briefing_project(Path(path))

    def _set_briefing_project(self, p: Path):
        self._briefing_project = p
        pill = self._pills.get("briefing_project")
        if pill:
            pill.set_text((p.name or str(p)) + "  ▾")

    def _set_briefing_mode(self, mode: str):
        if self._briefing_mode == mode:
            return
        self._briefing_mode = mode
        for spec_mode, color in (
            ("full",  LCARS["butterscotch"]),
            ("quick", LCARS["african_violet"]),
        ):
            pill = self._pills.get(f"brief_mode_{spec_mode}")
            if not pill:
                continue
            active = (spec_mode == mode)
            pill.set_color(
                color if active else "#1d1d2a",
                fg=LCARS["bg"] if active else color,
            )

    def _briefing_status(self, text: str) -> None:
        """Update the trigger pill's label from any thread."""
        pill = self._pills.get("briefing_trigger")
        if not pill:
            return
        # tk widgets must be touched from the main loop only.
        self.root.after(0, lambda: pill.set_text(text))

    def _trigger_briefing(self):
        # Prevent overlapping triggers.
        existing = getattr(self, "_briefing_thread", None)
        if existing is not None and existing.is_alive():
            self._briefing_status("◐ ALREADY RUNNING")
            self.root.after(1500, lambda: self._briefing_status("▶  TRIGGER BRIEFING"))
            return

        project = self._briefing_project
        mode = self._briefing_mode
        key = self.cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            self._briefing_status("✗ NO API KEY")
            self.root.after(2400, lambda: self._briefing_status("▶  TRIGGER BRIEFING"))
            return

        def _worker():
            import sys as _sys, threading as _th  # noqa: F401
            scripts_dir = ROOT / "scripts"
            if str(scripts_dir) not in _sys.path:
                _sys.path.insert(0, str(scripts_dir))
            if str(ROOT) not in _sys.path:
                _sys.path.insert(0, str(ROOT))
            try:
                import majel_briefing as mb  # type: ignore
                import computerize  # type: ignore

                cache_key = (str(project), mode)

                # Step 1 — gather context (cached after first read).
                ctx = self._briefing_ctx_cache.get(cache_key)
                if ctx is None:
                    self._briefing_status("◐ READING FILES…")
                    ctx = mb.gather_context(project, mode)
                    self._briefing_ctx_cache[cache_key] = ctx

                # Step 2 — call Claude (cached output).
                briefing = self._briefing_out_cache.get(cache_key)
                if briefing is None:
                    self._briefing_status("◐ THINKING…")
                    briefing = mb.call_claude(ctx, key, mode)
                    if briefing:
                        self._briefing_out_cache[cache_key] = briefing
                        # Persist immediately so the user can compare later
                        # even if speak.py never delivers audio.
                        try:
                            mb.save_briefing(project, mode, briefing, ctx)
                        except OSError:
                            pass
                if not briefing:
                    self._briefing_status("✗ EMPTY BRIEFING")
                    self.root.after(2400, lambda: self._briefing_status("▶  TRIGGER BRIEFING"))
                    return

                # Step 3 — pronunciation post-processing + speak.
                briefing_proc = computerize._post_process(briefing)
                self._briefing_status("◐ SPEAKING…")
                for chunk in mb.chunk_for_speak(briefing_proc):
                    mb.speak(chunk)

                self._briefing_status("✓ BRIEFING COMPLETE")
                self.root.after(3000, lambda: self._briefing_status("▶  TRIGGER BRIEFING"))
            except Exception as ex:
                with open("/tmp/majel_briefing.log", "a") as f:
                    import traceback
                    f.write(f"\n--- briefing error {time.time()} ---\n")
                    traceback.print_exc(file=f)
                self._briefing_status(f"✗ ERROR (see /tmp/majel_briefing.log)")
                self.root.after(3000, lambda: self._briefing_status("▶  TRIGGER BRIEFING"))

        import threading
        self._briefing_thread = threading.Thread(target=_worker, daemon=True)
        self._briefing_thread.start()
        self._briefing_status("◐ STARTING…")

    # ── Service status ────────────────────────────────────────────────────
    def _restart(self, script: str):
        kill_script(script)
        time.sleep(0.3)
        env = None
        if script == "background.py" and self.cfg["bg_group"]:
            env = {"MAJEL_BG_GROUP": self.cfg["bg_group"]}
        start_script(script, extra_env=env)
        self.root.after(400, self._refresh_status)

    def _refresh_status(self):
        for script, _ in DAEMONS.items():
            running = is_running(script)
            pill = self._svc_status_pills.get(script)
            if not pill:
                continue
            pill.set_text("ONLINE" if running else "OFFLINE")
            pill.set_color(LCARS["lima_bean"] if running else LCARS["red"],
                           fg=LCARS["bg"])

    def _refresh_bottom_bar(self):
        # Clock
        if "bot_clock" in self._pills:
            self._pills["bot_clock"].set_text(self._stardate())
        if hasattr(self, "_date_text"):
            self.canvas.itemconfig(self._date_text, text=self._stardate())
        # Voice mode
        mode = self.cfg.get("voice_mode", "offline").upper()
        if "bot_vmode" in self._pills:
            self._pills["bot_vmode"].set_text(f"MODE: {mode}")
        # Clip count
        n = trekdata_clip_count()
        if "bot_clips" in self._pills:
            self._pills["bot_clips"].set_text(f"CLIPS: {n}")
        # Service count
        ups = sum(1 for s in DAEMONS if is_running(s))
        if "bot_svc" in self._pills:
            self._pills["bot_svc"].set_text(f"SVC {ups}/{len(DAEMONS)}")
            color = LCARS["lima_bean"] if ups == len(DAEMONS) else (
                LCARS["butterscotch"] if ups > 0 else LCARS["red"])
            self._pills["bot_svc"].set_color(color, fg=LCARS["bg"])
        # Now playing
        if hasattr(self, "_now_playing"):
            self.canvas.itemconfig(self._now_playing, text=current_track_name())
        self.root.after(2000, self._refresh_bottom_bar)

    # ── Periodic poll ─────────────────────────────────────────────────────
    def _poll(self):
        self._refresh_status()
        save_cfg(self.cfg)
        self.root.after(2000, self._poll)


def main():
    root = tk.Tk()
    LCARSApp(root)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
