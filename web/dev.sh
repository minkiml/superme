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

# Load the single repo-root .env so all three children inherit the same config.
set -a; [ -f "$ROOT/.env" ] && . "$ROOT/.env"; set +a
DAEMON_PORT="${SUPERME_DAEMON_PORT:-8787}"
BFF_PORT="${SUPERME_BFF_PORT:-8000}"
FE_PORT="${VITE_PORT:-5173}"

echo "▸ starting Core daemon (:$DAEMON_PORT) …"
python -m superme_agent.daemon &
DAEMON_PID=$!

echo "▸ starting web BFF (:$BFF_PORT) …"
python -m web.bff &
BFF_PID=$!

# Stop the background processes when this script exits (Ctrl-C).
trap "echo; echo 'stopping…'; kill $DAEMON_PID $BFF_PID 2>/dev/null" EXIT

sleep 2
echo "▸ starting Vite frontend (:$FE_PORT) — open http://localhost:$FE_PORT"
cd "$ROOT/web/frontend"
npm run dev
