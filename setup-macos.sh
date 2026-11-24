#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Install Python 3.11 or newer from python.org, then run this script again."
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/python -m playwright install chromium

# Live configuration: ask once for the account name exactly as Momentum shows it (top right, before the colon).
CONFIG="${MOMENTUM_HOME:-$HOME/.momentum-followups}/config.json"
ACCOUNT="${MOMENTUM_ACCOUNT:-}"
CRM="${MOMENTUM_CRM:-}"
if [ ! -f "$CONFIG" ] && [ -t 0 ]; then
  [ -n "$CRM" ] || read -r -p "Your Momentum address, as in the browser bar (for example https://yourstore.udccrm.com): " CRM
  [ -n "$ACCOUNT" ] || read -r -p "Your name exactly as Momentum shows it at the top right (for example Alex Morgan Rivera): " ACCOUNT
fi
if [ -f "$CONFIG" ]; then
  echo "Keeping your existing configuration: $CONFIG"
elif [ -n "$ACCOUNT" ] && [ -n "$CRM" ]; then
  .venv/bin/python -m momentum.setup_cli --account "$ACCOUNT" --crm "$CRM"
else
  echo "Not configured yet. Run later: .venv/bin/python -m momentum.setup_cli --crm https://yourstore.udccrm.com --account \"Your Name\""
fi

# Copy the Chrome extension to a visible folder.
bash install-extension-macos.command || true
echo
echo "Setup complete. Next: load the extension in Chrome (chrome://extensions, Developer mode, Load unpacked,"
echo "folder ~/MomentumBridge-extension), then double-click start-macos.command."
