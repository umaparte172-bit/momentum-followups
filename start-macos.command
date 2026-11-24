#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run bash setup-macos.sh first."
  exit 1
fi
# Open the tool in the browser a moment after the server starts.
( sleep 3; open "http://127.0.0.1:8765" >/dev/null 2>&1 || true ) &
exec .venv/bin/python -m momentum.server
