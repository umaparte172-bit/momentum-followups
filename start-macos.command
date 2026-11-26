#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run bash setup-macos.sh first."
  exit 1
fi
bash stop-macos.sh
# Say clearly, before anything else, if this setup could not send.
.venv/bin/python -m momentum.setup_cli --check || echo "Run bash setup-macos.sh to fix this."
# Open the tool in the browser a moment after the server starts.
( sleep 3; open "http://127.0.0.1:8765" >/dev/null 2>&1 || true ) &
# A long list takes over an hour: keep the Mac and its screen awake while the tool is open.
if command -v caffeinate >/dev/null 2>&1; then
  exec caffeinate -di .venv/bin/python -m momentum.server
fi
exec .venv/bin/python -m momentum.server
