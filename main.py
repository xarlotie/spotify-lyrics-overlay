#!/usr/bin/env python3
"""
Spotify Lyrics Overlay — macOS

Timing model (inspired by LyricFever):
  Instead of polling every N ms and checking whether the lyric changed,
  we calculate exactly how long until the *next* lyric and call
  threading.Event.wait(timeout=<that duration>).  The thread wakes up
  precisely when the new line should appear — no drift, no polling lag.

  A separate 500 ms poll thread watches Spotify via AppleScript.
  When it detects a seek (position jumped unexpectedly by > 1.5 s) or a
  track change it sets a threading.Event that immediately wakes the sync
  thread so it can recalculate from the new position.

Lyrics sources (tried in order):
  1. Spotify's own color-lyrics API  — same timestamps Spotify itself uses
  2. lrclib.net direct HTTP          — free, no auth, very accurate
  3. syncedlyrics library            — Lrclib → Musixmatch → others
"""

import tkinter as tk
from tkinter import font as tkfont
import subprocess
import threading
import time
import re
import json
import urllib.parse
from pathlib import Path

try:
    import requests as _req
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import syncedlyrics
    HAS_SYNCED = True
except ImportError:
    HAS_SYNCED = False

# Prefer in-process AppleScript (NSAppleScript) so macOS ties the Automation
# permission to SpotifyLyrics.app rather than to /usr/bin/osascript.
try:
    from AppKit import NSAppleScript
    HAS_PYOBJC = True
except ImportError:
    HAS_PYOBJC = False

CONFIG_FILE = Path.home() / ".spotify_lyrics_overlay.json"

DEFAULT_CONFIG = {
    "x": None,
    "y": None,
    "width": 960,
    "font_size": 22,
    "opacity": 0.85,
    "tall_mode": False,
    "font_family": "Helvetica Neue",
    "text_color": "#FFFFFF",
    "bg_color": "#111111",
    "sync_offset": 0.0,
}

NORMAL_H    = 72
TALL_H      = 120
POLL_S      = 0.5      # AppleScript polling interval
SEEK_THRESH = 1.5      # seconds: position jump that counts as a user seek


class LyricsStrip:
    def __init__(self):
        self.cfg = self._load_config()

        # ── playback state ──────────────────────────────────────────────────
        self.current_track_key: str | None = None
        self.current_track_id:  str | None = None   # e.g. "4iV5W9uYEdYUVa79Axb7Rh"
        self.is_playing   = False
        self._miss_count  = 0
        self.lyrics_lines: list[tuple[float, str]] = []  # (seconds, text)
        self.current_line_text = ""

        # Interpolation anchors (updated by poll thread)
        self._anchor_pos : float = 0.0
        self._anchor_wall: float = 0.0   # time.time() at midpoint of AppleScript call
        self._state_lock = threading.Lock()

        # Wakes the lyric-sync thread on seek, track-change, or play/pause
        self._sync_event = threading.Event()

        # Permission guidance: track how many consecutive polls returned nothing
        self._consecutive_empty = 0
        self._ever_connected    = False

        # ── user controls ───────────────────────────────────────────────────
        self.sync_offset: float = float(self.cfg.get("sync_offset", 0.0))
        self.opacity    : float = float(self.cfg["opacity"])
        self.tall_mode  : bool  = bool(self.cfg["tall_mode"])

        self._drag_sx  = 0
        self._drag_sy  = 0
        self._flash_id = None
        self._running  = True

        # Cached Spotify access token
        self._sp_token: str | None = None
        self._sp_token_expires: float = 0.0

        self.root = tk.Tk()
        self._setup_window()
        self._setup_ui()
        self._start_threads()

    # ── Config ───────────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        cfg = DEFAULT_CONFIG.copy()
        if CONFIG_FILE.exists():
            try:
                cfg.update(json.loads(CONFIG_FILE.read_text()))
            except Exception:
                pass
        return cfg

    def _save_config(self):
        self.cfg.update(
            x=self.root.winfo_x(),
            y=self.root.winfo_y(),
            width=self.root.winfo_width(),
            opacity=self.opacity,
            tall_mode=self.tall_mode,
            font_size=int(self.main_font["size"]) if hasattr(self, "main_font") else 22,
            sync_offset=self.sync_offset,
        )
        try:
            CONFIG_FILE.write_text(json.dumps(self.cfg, indent=2))
        except Exception:
            pass

    # ── Window ───────────────────────────────────────────────────────────────

    def _setup_window(self):
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", self.opacity)
        self.root.configure(bg=self.cfg["bg_color"])

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        w  = int(self.cfg["width"])
        h  = TALL_H if self.tall_mode else NORMAL_H
        x  = int(self.cfg["x"]) if self.cfg["x"] is not None else (sw - w) // 2
        y  = int(self.cfg["y"]) if self.cfg["y"] is not None else sh - h - 60
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ── UI ───────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        bg    = self.cfg["bg_color"]
        fg    = self.cfg["text_color"]
        fsize = int(self.cfg["font_size"])
        fam   = self.cfg["font_family"]

        self.main_font = tkfont.Font(family=fam, size=fsize,          weight="bold")
        self.next_font = tkfont.Font(family=fam, size=max(fsize-5,10),weight="normal")
        self.hint_font = tkfont.Font(family=fam, size=9,              weight="normal")

        self.outer = tk.Frame(self.root, bg=bg)
        self.outer.pack(fill="both", expand=True)

        self.accent = tk.Frame(self.outer, bg="#1DB954", width=5)
        self.accent.pack(side="left", fill="y")
        self.accent.pack_propagate(False)

        self.content = tk.Frame(self.outer, bg=bg)
        self.content.pack(side="left", fill="both", expand=True)

        self.main_label = tk.Label(
            self.content, text="♫   Waiting for Spotify…",
            font=self.main_font, fg=fg, bg=bg,
            anchor="w", padx=18, pady=0,
        )
        self.main_label.pack(
            fill="both" if not self.tall_mode else "x",
            expand=not self.tall_mode,
        )

        self.next_label = tk.Label(
            self.content, text="",
            font=self.next_font, fg="#606060", bg=bg,
            anchor="w", padx=22, pady=0,
        )
        if self.tall_mode:
            self.next_label.pack(fill="x")

        self.ctrl_frame = tk.Frame(self.outer, bg=bg, width=56)
        self.ctrl_frame.pack(side="right", fill="y")
        self.ctrl_frame.pack_propagate(False)

        self.offset_label = tk.Label(
            self.ctrl_frame, text="", fg="#555", bg=bg, font=self.hint_font,
        )
        self.offset_label.pack(pady=(6, 0))

        close_btn = tk.Label(
            self.ctrl_frame, text="✕", fg="#444", bg=bg,
            font=tkfont.Font(size=13), cursor="hand2",
        )
        close_btn.pack(expand=True)
        close_btn.bind("<Button-1>", lambda _: self._quit())

        self._bind_events(
            self.outer, self.content, self.main_label,
            self.next_label, self.accent, self.ctrl_frame, self.offset_label,
        )
        self._build_menu()

        self.root.bind("<bracketleft>",  lambda _: self._nudge(-0.5))
        self.root.bind("<bracketright>", lambda _: self._nudge(+0.5))
        self.root.bind("<braceleft>",    lambda _: self._nudge(-2.0))
        self.root.bind("<braceright>",   lambda _: self._nudge(+2.0))
        self.root.bind("<BackSpace>",    lambda _: self._reset_offset())
        self.root.focus_set()

    def _bind_events(self, *widgets):
        for w in widgets:
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_move)
            w.bind("<Button-2>",      self._show_menu)
            w.bind("<Button-3>",      self._show_menu)
            w.bind("<MouseWheel>",    self._on_scroll)
            w.bind("<Enter>",         lambda _: self._hover(True))
            w.bind("<Leave>",         lambda _: self._hover(False))

    def _build_menu(self):
        bg, fg, abg = "#1e1e1e", "#eeeeee", "#2d2d2d"
        m = tk.Menu(self.root, tearoff=0, bg=bg, fg=fg,
                    activebackground=abg, activeforeground=fg, bd=0, relief="flat")
        m.add_command(label="  ▲  Font Larger         ↑ scroll", command=self._font_up)
        m.add_command(label="  ▼  Font Smaller        ↓ scroll", command=self._font_down)
        m.add_separator()
        m.add_command(label="  ◑  More Opaque",                  command=self._opacity_up)
        m.add_command(label="  ◐  More Transparent",             command=self._opacity_down)
        m.add_separator()
        m.add_command(label="  ⇕  Toggle Tall Mode",             command=self._toggle_tall)
        m.add_separator()
        m.add_command(label="  ↔  Narrow  (600 px)",  command=lambda: self._set_width(600))
        m.add_command(label="  ↔  Medium  (960 px)",  command=lambda: self._set_width(960))
        m.add_command(label="  ↔  Wide   (1280 px)",  command=lambda: self._set_width(1280))
        m.add_command(label="  ↔  Full Screen Width", command=self._set_width_full)
        m.add_separator()
        m.add_command(label="  ◁  Sync Earlier  [ (-0.5 s)", command=lambda: self._nudge(-0.5))
        m.add_command(label="  ▷  Sync Later    ] (+0.5 s)", command=lambda: self._nudge(+0.5))
        m.add_command(label="  ○  Reset Sync  ⌫",            command=self._reset_offset)
        m.add_separator()
        m.add_command(label="  ⟳  Re-fetch Lyrics",           command=self._refetch)
        m.add_separator()
        m.add_command(label="  ✕  Quit",                      command=self._quit)
        self.menu = m

    # ── Hover / drag / menu ──────────────────────────────────────────────────

    def _hover(self, entering: bool):
        col = "#cccccc" if entering else "#444444"
        for w in self.ctrl_frame.winfo_children():
            if w is not self.offset_label:
                w.configure(fg=col)

    def _drag_start(self, e):
        self._drag_sx = e.x_root - self.root.winfo_x()
        self._drag_sy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._drag_sx}+{e.y_root - self._drag_sy}")

    def _show_menu(self, e):
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    # ── Font / opacity / geometry ────────────────────────────────────────────

    def _font_up(self):
        s = min(self.main_font["size"] + 2, 54)
        self.main_font.configure(size=s)
        self.next_font.configure(size=max(s - 5, 10))

    def _font_down(self):
        s = max(self.main_font["size"] - 2, 10)
        self.main_font.configure(size=s)
        self.next_font.configure(size=max(s - 5, 10))

    def _on_scroll(self, e):
        (self._font_up if e.delta > 0 else self._font_down)()

    def _opacity_up(self):
        self.opacity = min(round(self.opacity + 0.1, 1), 1.0)
        self.root.wm_attributes("-alpha", self.opacity)

    def _opacity_down(self):
        self.opacity = max(round(self.opacity - 0.1, 1), 0.15)
        self.root.wm_attributes("-alpha", self.opacity)

    def _toggle_tall(self):
        self.tall_mode = not self.tall_mode
        x, y, w = self.root.winfo_x(), self.root.winfo_y(), self.root.winfo_width()
        self.root.geometry(f"{w}x{TALL_H if self.tall_mode else NORMAL_H}+{x}+{y}")
        if self.tall_mode:
            self.main_label.pack_configure(fill="x", expand=False)
            self.next_label.pack(fill="x")
        else:
            self.next_label.pack_forget()
            self.main_label.pack_configure(fill="both", expand=True)

    def _set_width(self, w: int):
        self.root.geometry(
            f"{w}x{self.root.winfo_height()}+{self.root.winfo_x()}+{self.root.winfo_y()}"
        )

    def _set_width_full(self):
        self._set_width(self.root.winfo_screenwidth())

    # ── Sync offset ──────────────────────────────────────────────────────────

    def _nudge(self, delta: float):
        self.sync_offset = round(self.sync_offset + delta, 1)
        self._draw_offset_label()
        self._sync_event.set()   # recalculate immediately

    def _reset_offset(self):
        self.sync_offset = 0.0
        self._draw_offset_label()
        self._sync_event.set()

    def _draw_offset_label(self):
        if self.sync_offset == 0.0:
            self.offset_label.configure(text="", fg="#555")
        else:
            sign = "+" if self.sync_offset > 0 else ""
            self.offset_label.configure(text=f"{sign}{self.sync_offset:.1f}s", fg="#f0a500")

    # ── Flash ────────────────────────────────────────────────────────────────

    def _flash(self):
        if self._flash_id:
            self.root.after_cancel(self._flash_id)
        self.main_label.configure(fg="#1DB954")
        self._flash_id = self.root.after(
            90, lambda: self.main_label.configure(fg=self.cfg["text_color"])
        )

    # ── Spotify via AppleScript ──────────────────────────────────────────────

    _SPOTIFY_SCRIPT = """
tell application "System Events"
    if not (exists process "Spotify") then return "GONE"
end tell
tell application "Spotify"
    if player state is playing then
        return "PLAYING|||" & (id of current track) & "|||" & (name of current track) & "|||" & (artist of current track) & "|||" & (player position as text)
    else if player state is paused then
        return "PAUSED|||" & (id of current track) & "|||" & (name of current track) & "|||" & (artist of current track) & "|||" & (player position as text)
    else
        return "STOPPED"
    end if
end tell
"""

    @staticmethod
    def _run_applescript(source: str) -> str:
        """
        Run AppleScript in-process via NSAppleScript (pyobjc) when available.
        macOS then prompts for Automation permission under SpotifyLyrics.app's
        bundle ID rather than under /usr/bin/osascript.
        Falls back to subprocess osascript if pyobjc is unavailable.
        """
        if HAS_PYOBJC:
            try:
                script = NSAppleScript.alloc().initWithSource_(source)
                result, error = script.executeAndReturnError_(None)
                if error:
                    return ""
                return result.stringValue() if result else ""
            except Exception:
                return ""
        else:
            try:
                r = subprocess.run(
                    ["osascript", "-e", source],
                    capture_output=True, text=True, timeout=4,
                )
                return r.stdout.strip()
            except Exception:
                return ""

    @staticmethod
    def _parse_spotify_output(out: str, wall_mid: float) -> dict | None:
        out = out.strip()
        if out in ("GONE", "STOPPED", ""):
            return None
        parts = out.split("|||")
        if len(parts) == 5:
            raw_id   = parts[1].strip()
            track_id = raw_id.split(":")[-1] if ":" in raw_id else raw_id
            return {
                "playing"  : parts[0] == "PLAYING",
                "track_id" : track_id,
                "track"    : parts[2].strip(),
                "artist"   : parts[3].strip(),
                "position" : float(parts[4].strip()),
                "wall"     : wall_mid,
            }
        return None

    def _query_spotify(self) -> dict | None:
        t_before = time.time()
        out      = self._run_applescript(self._SPOTIFY_SCRIPT)
        t_after  = time.time()
        return self._parse_spotify_output(out, (t_before + t_after) / 2)

    # ── Interpolated position ────────────────────────────────────────────────

    def _estimated_pos(self) -> float:
        with self._state_lock:
            if not self.is_playing or self._anchor_wall == 0.0:
                return self._anchor_pos
            return self._anchor_pos + (time.time() - self._anchor_wall)

    # ── Lyrics providers ─────────────────────────────────────────────────────

    def _fetch_lyrics_bg(self, track_id: str, track: str, artist: str):
        """Try providers in order; store result in self.lyrics_lines."""
        lines = None

        # 1. Spotify's own lyrics API (same timestamps Spotify uses internally)
        if HAS_REQUESTS and track_id:
            lines = self._fetch_spotify_lyrics(track_id)

        # 2. lrclib.net direct HTTP
        if not lines and HAS_REQUESTS:
            lines = self._fetch_lrclib(track, artist)

        # 3. syncedlyrics library (Lrclib → Musixmatch → …)
        if not lines and HAS_SYNCED:
            lines = self._fetch_syncedlyrics(track, artist)

        if lines:
            self.lyrics_lines = lines
            self._sync_event.set()   # wake sync thread immediately
        else:
            self.lyrics_lines = []
            self.root.after(0, lambda: self.main_label.configure(
                text=f"♫   {track}  —  {artist}"
            ))

    # 1 ── Spotify color-lyrics API ───────────────────────────────────────────

    def _spotify_access_token(self) -> str | None:
        now = time.time()
        if self._sp_token and now < self._sp_token_expires:
            return self._sp_token
        try:
            r = _req.get(
                "https://open.spotify.com/api/token",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=5,
            )
            if r.ok:
                data = r.json()
                self._sp_token = data.get("accessToken")
                exp = data.get("accessTokenExpirationTimestampMs", 0)
                self._sp_token_expires = exp / 1000.0 - 30   # 30 s buffer
                return self._sp_token
        except Exception:
            pass
        return None

    def _fetch_spotify_lyrics(self, track_id: str) -> list[tuple[float, str]] | None:
        token = self._spotify_access_token()
        if not token:
            return None
        try:
            r = _req.get(
                f"https://spclient.wg.spotify.com/color-lyrics/v2/track/{track_id}",
                params={"format": "json", "vocalRemoval": "false", "market": "from_token"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "app-platform": "WebPlayer",
                    "User-Agent": "Mozilla/5.0",
                },
                timeout=6,
            )
            if r.ok:
                data  = r.json()
                lyric = data.get("lyrics", {})
                if lyric.get("syncType") == "LINE_SYNCED":
                    lines = []
                    for ln in lyric.get("lines", []):
                        ms   = int(ln.get("startTimeMs", 0))
                        text = ln.get("words", "").strip()
                        if text and text != "♪":
                            lines.append((ms / 1000.0, text))
                    return lines or None
        except Exception:
            pass
        return None

    # 2 ── lrclib.net direct HTTP ─────────────────────────────────────────────

    def _fetch_lrclib(self, track: str, artist: str) -> list[tuple[float, str]] | None:
        try:
            q = urllib.parse.urlencode({"track_name": track, "artist_name": artist})
            r = _req.get(
                f"https://lrclib.net/api/get?{q}",
                headers={"User-Agent": "SpotifyLyricsOverlay/2.0"},
                timeout=6,
            )
            if r.ok:
                data = r.json()
                lrc  = data.get("syncedLyrics") or ""
                if lrc:
                    return self._parse_lrc(lrc)
        except Exception:
            pass
        return None

    # 3 ── syncedlyrics library ───────────────────────────────────────────────

    def _fetch_syncedlyrics(self, track: str, artist: str) -> list[tuple[float, str]] | None:
        for providers in (["Lrclib"], ["Musixmatch"], []):
            try:
                kwargs = {"synced_only": True}
                if providers:
                    kwargs["providers"] = providers
                lrc = syncedlyrics.search(f"{track} {artist}", **kwargs)
                if lrc:
                    lines = self._parse_lrc(lrc)
                    if lines:
                        return lines
            except Exception:
                pass
        return None

    # ── LRC parser ───────────────────────────────────────────────────────────

    @staticmethod
    def _parse_lrc(lrc_text: str) -> list[tuple[float, str]]:
        lines: list[tuple[float, str]] = []
        pat = re.compile(r"\[(\d+):(\d+(?:[.:]\d+)?)\](.*)")
        for raw in lrc_text.splitlines():
            m = pat.match(raw.strip())
            if m:
                mins    = int(m.group(1))
                sec_str = m.group(2).replace(":", ".")
                text    = m.group(3).strip()
                if text:
                    lines.append((mins * 60 + float(sec_str), text))
        return sorted(lines, key=lambda x: x[0])

    def _line_at(self, pos: float) -> tuple[int, str, str]:
        """Return (index, current_text, next_text) for the given position."""
        ll = self.lyrics_lines
        if not ll:
            return -1, "", ""
        lo, hi, cur = 0, len(ll) - 1, -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if ll[mid][0] <= pos:
                cur = mid
                lo  = mid + 1
            else:
                hi  = mid - 1
        if cur < 0:
            return -1, "", ll[0][1] if ll else ""
        nxt = ll[cur + 1][1] if cur + 1 < len(ll) else ""
        return cur, ll[cur][1], nxt

    # ── Refetch ──────────────────────────────────────────────────────────────

    def _refetch(self):
        self.current_track_key = None
        self.current_track_id  = None
        self.lyrics_lines      = []
        self.current_line_text = ""
        self._sync_event.set()
        self.main_label.configure(text="♫   Re-fetching lyrics…")

    # ── Permission guidance ───────────────────────────────────────────────────

    def _show_permission_guidance(self):
        """
        Called after several seconds of empty AppleScript results with no
        prior successful query — almost always means Automation permission
        hasn't been granted.  Show a clear message and open System Settings.
        """
        msg = "⚠️  Allow Spotify access: System Settings → Privacy & Security → Automation → SpotifyLyrics ✓"
        self.main_label.configure(text=msg)
        self.next_label.configure(text="")
        # Open Automation pane directly so the user can flip the switch
        try:
            subprocess.Popen([
                "open",
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
            ])
        except Exception:
            pass

    # ── Spotify poll thread ───────────────────────────────────────────────────

    def _poll_loop(self):
        """
        Polls Spotify every POLL_S seconds.
        Detects: track change, seek, play/pause transitions.
        Updates interpolation anchors and signals the sync thread.
        """
        while self._running:
            try:
                state = self._query_spotify()

                if state:
                    self._miss_count       = 0
                    self._consecutive_empty = 0
                    self._ever_connected    = True
                    new_pos  = state["position"]
                    new_wall = state["wall"]
                    key      = f"{state['track']}||{state['artist']}"

                    with self._state_lock:
                        was_playing = self.is_playing
                        self.is_playing = state["playing"]

                        # Detect seek: expected pos vs actual pos differ by > threshold
                        if was_playing and self._anchor_wall:
                            expected = self._anchor_pos + (new_wall - self._anchor_wall)
                            seek_detected = abs(new_pos - expected) > SEEK_THRESH
                        else:
                            seek_detected = False

                        self._anchor_pos  = new_pos
                        self._anchor_wall = new_wall if state["playing"] else 0.0

                    if key != self.current_track_key:
                        # ── New song ──────────────────────────────────────
                        self.current_track_key = key
                        self.current_track_id  = state["track_id"]
                        self.lyrics_lines      = []
                        self.current_line_text = ""
                        n = state["track"]
                        self.root.after(0, lambda name=n: self.main_label.configure(
                            text=f"♫   Loading lyrics…  {name}"
                        ))
                        self.root.after(0, lambda: self.next_label.configure(text=""))
                        threading.Thread(
                            target=self._fetch_lyrics_bg,
                            args=(state["track_id"], state["track"], state["artist"]),
                            daemon=True,
                        ).start()
                        self._sync_event.set()

                    elif seek_detected or (state["playing"] != was_playing):
                        # ── Seek or play/pause ────────────────────────────
                        self._sync_event.set()

                else:
                    self._miss_count        += 1
                    self._consecutive_empty += 1

                    # After ~5 s of empty results with no prior success, almost
                    # certainly an Automation permission problem — guide the user.
                    if not self._ever_connected and self._consecutive_empty == 10:
                        self.root.after(0, self._show_permission_guidance)

                    if self._miss_count >= 3:
                        with self._state_lock:
                            self.is_playing = False
                        if self.current_track_key is not None:
                            self.current_track_key = None
                            self.lyrics_lines      = []
                            self.current_line_text = ""
                            self.root.after(0, lambda: self.main_label.configure(
                                text="♫   Spotify not playing"
                            ))
                            self.root.after(0, lambda: self.next_label.configure(text=""))
                            self._sync_event.set()

            except Exception:
                pass

            time.sleep(POLL_S)

    # ── Lyric sync thread (the LyricFever-style sleep-until-next approach) ───

    def _lyric_sync_loop(self):
        """
        Core timing engine.

        For each iteration:
          1. Find which lyric should be showing right now.
          2. Display it (if it changed).
          3. Calculate exactly how many seconds until the NEXT lyric.
          4. Sleep for that duration via Event.wait(timeout=…).
             — wakes early if _sync_event is set (seek / track-change / nudge).
          5. Repeat.

        This gives millisecond-accurate lyric transitions without any polling.
        """
        while self._running:
            self._sync_event.clear()

            if not self.lyrics_lines or not self.is_playing:
                # Nothing to do; wait for something to change
                self._sync_event.wait(timeout=0.2)
                continue

            pos = self._estimated_pos() + self.sync_offset
            ll  = self.lyrics_lines   # local snapshot (thread-safe: list replace is atomic)

            idx, cur_text, nxt_text = self._line_at(pos)

            # Display current lyric if it changed
            if cur_text and cur_text != self.current_line_text:
                self.current_line_text = cur_text
                self.root.after(0, lambda c=cur_text, n=nxt_text:
                    (self.main_label.configure(text=c),
                     self.next_label.configure(text=n),
                     self._flash()))

            # Calculate sleep duration until the next lyric's timestamp
            next_idx = idx + 1
            if next_idx < len(ll):
                next_t   = ll[next_idx][0]
                sleep_s  = next_t - (self._estimated_pos() + self.sync_offset)
                if sleep_s < 0.005:
                    # Already past due — loop immediately
                    continue
                # Sleep precisely; wake early on seek/track-change/nudge
                self._sync_event.wait(timeout=sleep_s)
            else:
                # Waiting after last lyric (or before the first)
                self._sync_event.wait(timeout=0.5)

    # ── Start threads ─────────────────────────────────────────────────────────

    def _start_threads(self):
        threading.Thread(target=self._poll_loop,       daemon=True).start()
        threading.Thread(target=self._lyric_sync_loop, daemon=True).start()

    # ── Quit ─────────────────────────────────────────────────────────────────

    def _quit(self):
        self._running = False
        self._sync_event.set()
        self._save_config()
        self.root.quit()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = LyricsStrip()
    app.run()
