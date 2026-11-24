#!/bin/bash
# Copies the Momentum Bridge extension to a visible folder so it can be loaded in Chrome ("Load unpacked").
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run bash setup-macos.sh first."
  exit 1
fi
SRC="$(.venv/bin/python -c 'import os, momentum; print(os.path.join(os.path.dirname(momentum.__file__), "extension"))')"
DEST="$HOME/MomentumBridge-extension"
rm -rf "$DEST"
cp -R "$SRC" "$DEST"
echo "Extension copied to: $DEST"
echo
echo "In Chrome: open chrome://extensions, turn on Developer mode, click Load unpacked and choose that folder."
if command -v open >/dev/null 2>&1; then open -R "$DEST" || true; fi
