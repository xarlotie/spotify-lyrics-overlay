#!/usr/bin/env bash
# Spotify Lyrics Overlay — setup & launch
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Check Python 3
if ! command -v python3 &>/dev/null; then
  echo "❌  Python 3 not found. Install it from https://python.org or via Homebrew."
  exit 1
fi

# Check tkinter
if ! python3 -c "import tkinter" 2>/dev/null; then
  echo "❌  tkinter not found."
  echo "   If using Homebrew Python, run:  brew install python-tk"
  echo "   If using the python.org installer, tkinter is included."
  exit 1
fi

# Create venv if needed
VENV="$SCRIPT_DIR/.venv"
if [ ! -d "$VENV" ]; then
  echo "→  Creating virtual environment…"
  python3 -m venv "$VENV"
fi

# Install/upgrade deps
echo "→  Installing dependencies…"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"

echo "→  Launching Spotify Lyrics Overlay…"
echo "   Right-click the strip for options | Scroll to resize font | Drag to move"
exec "$VENV/bin/python" "$SCRIPT_DIR/main.py"
