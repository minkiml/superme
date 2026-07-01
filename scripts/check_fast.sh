#!/usr/bin/env bash
# Fast, read-only refactor gate — run CONSTANTLY between edits during the Backend Refactor (R0–R8).
# Asserts the daemon's route surface is unchanged + the frontend still typechecks. Seconds, no mutation.
#
#   bash scripts/check_fast.sh                 # inventory gate + shapes-info + ws + FE tsc
#   STRICT=1 bash scripts/check_fast.sh        # also fail on ANY OpenAPI shape drift (pure-move stages)
#
# Heavier checks (the self-cleaning E2E in scripts/test_*.py) run at the stage boundaries that touch them.
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${SUPERME_PY:-/opt/homebrew/Caskroom/miniconda/base/envs/my-agent/bin/python}"
EXTRA=""; [ "${STRICT:-0}" = "1" ] && EXTRA="--strict-shapes"

# Import the app FRESH (not the running daemon) so a startup-breaking edit — a bad relative import,
# a syntax error — fails the gate even when a stale daemon is still serving the old code on the port.
# (Parity below hits the LIVE daemon; without this, a silently-failed restart would gate green.)
echo "▸ import check (fresh app load)"
PYTHONPATH=. "$PY" -c "from superme_agent.daemon import server; assert server.app" ; IMPORT=$?

echo "▸ parity check ${EXTRA}"
PYTHONPATH=. "$PY" -m scripts.parity check $EXTRA; PARITY=$?

echo "▸ frontend typecheck"
( cd web/frontend && npx -y tsc --noEmit ); TSC=$?

echo "————"
if [ $IMPORT -eq 0 ] && [ $PARITY -eq 0 ] && [ $TSC -eq 0 ]; then
  echo "✓ FAST GATE GREEN"; exit 0
else
  echo "✗ FAST GATE RED  (import=$IMPORT parity=$PARITY tsc=$TSC)"; exit 1
fi
