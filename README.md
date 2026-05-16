# Spotify Lyrics Overlay

A floating, always-on-top lyrics strip for macOS that displays synced lyrics for whatever's playing on Spotify — so you can sing along while using any other app.

![Dark strip with Spotify-green accent, showing lyrics in white text](https://img.shields.io/badge/macOS-12%2B-black?logo=apple) ![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python) ![License: MIT](https://img.shields.io/badge/License-MIT-green)

## Features

- **Synced lyrics** — millisecond-accurate line transitions (sleep-until-next-lyric engine, inspired by [LyricFever](https://github.com/aviwad/LyricFever))
- **Always on top** — floats over any window while you work
- **Drag to reposition** — click and drag anywhere on the strip
- **Scroll to resize font** — mouse wheel adjusts font size instantly
- **Tall mode** — shows the current line + the next line below it
- **Opacity control** — right-click menu or keyboard
- **Sync nudge** — `[` / `]` to shift lyrics ±0.5 s if a track's LRC data is slightly off
- **Width presets** — Narrow / Medium / Wide / Full Screen
- **Settings persist** — position, font size, opacity, and sync offset saved automatically

## Lyrics sources (tried in order)

1. **Spotify color-lyrics API** — same timestamps Spotify itself uses (requires anonymous token)
2. **lrclib.net** — free, open, very accurate
3. **syncedlyrics library** — Lrclib → Musixmatch → others

## Requirements

- macOS 12 or later (Intel or Apple Silicon via Rosetta 2)
- Python 3.10+ (for building from source)
- Spotify desktop app

## Quick start (build from source)

```bash
git clone https://github.com/xarlotie/spotify-lyrics-overlay.git
cd spotify-lyrics-overlay
bash run.sh          # sets up venv, installs deps, launches the app
```

## Build a portable .app

```bash
bash build_app.sh
# → SpotifyLyrics.app appears in the project folder
```

Double-click to launch. Drag to your Dock or Applications folder.  
First launch: right-click → **Open** to bypass the macOS Gatekeeper prompt (app is not notarised).

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| `[` | Lyrics 0.5 s earlier |
| `]` | Lyrics 0.5 s later |
| `{` | Lyrics 2 s earlier |
| `}` | Lyrics 2 s later |
| `⌫ Backspace` | Reset sync offset |
| Scroll ↑ | Font larger |
| Scroll ↓ | Font smaller |

Right-click the strip for the full menu (tall mode, opacity, width, re-fetch, quit).

## How it works

The app uses **AppleScript** to query Spotify's current track and playback position every 500 ms. Between polls it extrapolates the position using `time.time()` so the display stays accurate between queries.

Lyrics are fetched once per track in a background thread. The sync engine then calculates exactly how many milliseconds remain until the next lyric line starts, sleeps for that duration using `threading.Event.wait(timeout=…)`, and wakes up precisely when the new line is due — no polling lag, no drift.

## License

MIT
