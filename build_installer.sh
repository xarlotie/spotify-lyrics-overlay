#!/usr/bin/env bash
# build_installer.sh
# Builds SpotifyLyrics-Installer.pkg — a standard macOS installer package.
# On any Mac: double-click the .pkg, click through the installer, done.
set -e

PROJ="$(cd "$(dirname "$0")" && pwd)"
APP="$PROJ/SpotifyLyrics.app"
PKG_OUT="$PROJ/SpotifyLyrics-Installer.pkg"
STAGE="$PROJ/_pkg_stage"

# ── 1. Build the .app first ───────────────────────────────────────────────
echo "→  Building SpotifyLyrics.app…"
bash "$PROJ/build_app.sh"

# ── 2. Stage the .app ─────────────────────────────────────────────────────
echo "→  Staging for packaging…"
rm -rf "$STAGE"
mkdir -p "$STAGE/Applications"
ditto "$APP" "$STAGE/Applications/SpotifyLyrics.app"

# Strip quarantine from the staged copy so it never blocks on install
xattr -dr com.apple.quarantine "$STAGE/Applications/SpotifyLyrics.app" 2>/dev/null || true

# ── 3. Build the .pkg ─────────────────────────────────────────────────────
echo "→  Building installer package…"
rm -f "$PKG_OUT"

pkgbuild \
  --root "$STAGE" \
  --identifier "com.charlotte.spotifylyrics" \
  --version "1.0" \
  --install-location "/" \
  "$PKG_OUT"

# ── 4. Clean up ───────────────────────────────────────────────────────────
rm -rf "$STAGE"

echo ""
echo "✓  Installer ready: $PKG_OUT"
echo ""
echo "To install on any Mac:"
echo "  1. Copy SpotifyLyrics-Installer.pkg to the target Mac"
echo "  2. Double-click it (or right-click → Open if Gatekeeper blocks)"
echo "  3. Click through the installer — app lands in /Applications"
echo "  4. First launch: macOS will ask permission to control Spotify — click OK"
