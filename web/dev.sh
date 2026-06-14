#!/usr/bin/env bash
# Launch the full SuperMe web stack for local dev:
#   daemon (:8787)  +  BFF (:8000)  +  Vite frontend (:5173)
#
# Run from anywhere, with the `my-agent` conda env active and Node installed:
#   conda activate my-agent
#   bash web/dev.sh
# Then open http://localhost:5173 . Ctrl-C stops all three.

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "▸ starting Core daemon (:8787) …"
python -m superme_agent.daemon &
DAEMON_PID=$!

echo "▸ starting web BFF (:8000) …"
python -m web.bff &
BFF_PID=$!

# Stop the background processes when this script exits (Ctrl-C).
trap "echo; echo 'stopping…'; kill $DAEMON_PID $BFF_PID 2>/dev/null" EXIT

sleep 2
echo "▸ starting Vite frontend (:5173) — open http://localhost:5173"
cd "$ROOT/web/frontend"
npm run dev
