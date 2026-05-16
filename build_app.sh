#!/usr/bin/env bash
# build_app.sh — builds a self-contained SpotifyLyrics.app using PyInstaller.
# The resulting .app bundles Python + all dependencies inside itself.
# Copy it to any Intel Mac running macOS 12+ — no setup required.
set -e

PROJ="$(cd "$(dirname "$0")" && pwd)"
VENV="$PROJ/.venv"

# ── 0. Virtual environment + dependencies ─────────────────────────────────
if [ ! -d "$VENV" ]; then
  echo "→  Creating virtual environment…"
  python3 -m venv "$VENV"
fi
echo "→  Installing / upgrading dependencies…"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$PROJ/requirements.txt"
"$VENV/bin/pip" install -q Pillow pyinstaller

# ── 1. Generate icon ──────────────────────────────────────────────────────
echo "→  Generating icon…"
"$VENV/bin/python" "$PROJ/make_icon.py"

# ── 2. Build .icns ────────────────────────────────────────────────────────
echo "→  Building .icns…"
ICONSET="$PROJ/AppIcon.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"
for SIZE in 16 32 128 256 512; do
  sips -z "$SIZE" "$SIZE" "$PROJ/AppIcon.png" \
       --out "$ICONSET/icon_${SIZE}x${SIZE}.png"         > /dev/null 2>&1
  D=$((SIZE * 2))
  sips -z "$D" "$D" "$PROJ/AppIcon.png" \
       --out "$ICONSET/icon_${SIZE}x${SIZE}@2x.png"      > /dev/null 2>&1
done
iconutil -c icns "$ICONSET" -o "$PROJ/AppIcon.icns"
rm -rf "$ICONSET"

# ── 3. PyInstaller build ──────────────────────────────────────────────────
echo "→  Bundling with PyInstaller (this takes ~30 s)…"
rm -rf "$PROJ/build" "$PROJ/dist" "$PROJ/SpotifyLyrics.app"

"$VENV/bin/pyinstaller" \
  --noconfirm \
  --windowed \
  --onedir \
  --name "SpotifyLyrics" \
  --icon "$PROJ/AppIcon.icns" \
  --osx-bundle-identifier "com.charlotte.spotifylyrics" \
  --add-data "$PROJ/AppIcon.icns:." \
  --hidden-import syncedlyrics \
  --hidden-import requests \
  --hidden-import certifi \
  --hidden-import charset_normalizer \
  --hidden-import idna \
  --hidden-import urllib3 \
  --hidden-import beautifulsoup4 \
  --hidden-import rapidfuzz \
  "$PROJ/main.py" \
  > /dev/null 2>&1

# ── 4. Move finished .app to project root ─────────────────────────────────
mv "$PROJ/dist/SpotifyLyrics.app" "$PROJ/SpotifyLyrics.app"

# Patch Info.plist with friendlier display name + privacy string
PLIST="$PROJ/SpotifyLyrics.app/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName 'Spotify Lyrics'"   "$PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string 'Spotify Lyrics'" "$PLIST"

/usr/libexec/PlistBuddy -c \
  "Add :NSAppleEventsUsageDescription string 'Spotify Lyrics reads the currently playing track from Spotify.'" \
  "$PLIST" 2>/dev/null || true

/usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion '12.0'" "$PLIST" 2>/dev/null || \
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string '12.0'" "$PLIST"

# ── 5. Clean up build artefacts ───────────────────────────────────────────
rm -rf "$PROJ/build" "$PROJ/dist" "$PROJ/SpotifyLyrics.spec"

echo ""
echo "✓  Built:  $PROJ/SpotifyLyrics.app"
echo ""
echo "To share:"
echo "  cd \"$PROJ\" && zip -r SpotifyLyrics.zip SpotifyLyrics.app"
echo "  → Send SpotifyLyrics.zip to anyone with an Intel Mac running macOS 12+"
echo ""
echo "First-launch note (on any Mac):"
echo "  Right-click the .app → Open → click Open (one-time security prompt)"
echo "  Or: System Settings → Privacy & Security → 'Open Anyway'"
