#!/bin/bash
# Stops a copy of the tool that is already running, so an older build never keeps answering on the same address.
# It never interrupts a run that is sending, and never touches another program.
set -euo pipefail
PORT="${MOMENTUM_PORT:-8765}"
PIDS="$(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
[ -n "$PIDS" ] || exit 0
for PID in $PIDS; do
  if ! ps -p "$PID" -o command= | grep -Eq 'momentum[.]server|momentum-followups'; then
    echo "Another program is using port $PORT (process $PID). Close it, then try again."
    exit 1
  fi
done
STATE="$(curl -s -m 5 "http://127.0.0.1:$PORT/api/state" || true)"
if printf '%s' "$STATE" | grep -q '"busy": true'; then
  echo "The tool is in the middle of a run. Stop it in the dashboard first, then try again."
  exit 1
fi
echo "Closing the copy of the tool that is already running..."
for PID in $PIDS; do kill -INT "$PID" 2>/dev/null || true; done
for _ in 1 2 3 4 5 6 7 8 9 10; do
  lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || exit 0
  sleep 1
done
echo "The running copy did not close. Press Ctrl+C in its Terminal window, then try again."
exit 1
