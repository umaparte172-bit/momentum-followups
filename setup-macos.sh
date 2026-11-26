#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Install Python 3.11 or newer from python.org, then run this script again."
  exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
# An older copy that is still running would keep serving the old build.
bash stop-macos.sh
# Files left over from an earlier build must not end up in this one.
rm -rf build momentum_followups.egg-info
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install .
.venv/bin/python -m pip install --force-reinstall --no-deps --no-cache-dir .
# Only the optional automation browser needs this; the Chrome extension works without it.
.venv/bin/python -m playwright install chromium || echo "The optional automation browser could not be installed. Sign in to CRM (the Chrome extension) still works."

# Live configuration: ask once for the account name exactly as Momentum shows it (top right, before the colon).
CONFIG="${MOMENTUM_HOME:-$HOME/.momentum-followups}/config.json"
ACCOUNT="${MOMENTUM_ACCOUNT:-}"
CRM="${MOMENTUM_CRM:-}"
if .venv/bin/python -m momentum.setup_cli --check; then
  echo "Keeping your existing configuration: $CONFIG"
else
  if [ -t 0 ]; then
    [ -n "$CRM" ] || read -r -p "Your Momentum address, as in the browser bar (for example https://yourstore.udccrm.com): " CRM
    [ -n "$ACCOUNT" ] || read -r -p "Your name exactly as Momentum shows it at the top right (for example Alex Morgan Rivera): " ACCOUNT
  fi
  if [ -n "$ACCOUNT" ] && [ -n "$CRM" ]; then
    .venv/bin/python -m momentum.setup_cli --account "$ACCOUNT" --crm "$CRM" --force
  else
    echo "Not configured yet. Run later: .venv/bin/python -m momentum.setup_cli --crm https://yourstore.udccrm.com --account \"Your Name\" --force"
  fi
fi

# Copy the Chrome extension to a visible folder.
bash install-extension-macos.command || true
echo
.venv/bin/python -c 'import momentum; from momentum.bridge import expected_version; print("Installed build", momentum.VERSION, "with Momentum Bridge", expected_version())'
echo "Next: in chrome://extensions click the reload arrow on Momentum Bridge (first install: Developer mode, Load unpacked,"
echo "folder ~/MomentumBridge-extension), reload your Momentum tab, then run: bash start-macos.command"
