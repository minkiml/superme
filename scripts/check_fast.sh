#!/usr/bin/env bash
# Fast, read-only gate to run between edits: the daemon's route surface is unchanged and
# the frontend still typechecks. Seconds, and it mutates nothing.
#
#   bash scripts/check_fast.sh                 # inventory gate + shapes-info + ws + FE tsc
#   STRICT=1 bash scripts/check_fast.sh        # also fail on ANY OpenAPI shape drift
#
# The heavier E2E suites in scripts/test_*.py run only when a change reaches them.
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${SUPERME_PY:-/opt/homebrew/Caskroom/miniconda/base/envs/my-agent/bin/python}"
EXTRA=""; [ "${STRICT:-0}" = "1" ] && EXTRA="--strict-shapes"

# Load the app fresh, because parity below hits the LIVE daemon: without this, a stale
# daemon still serving old code would let a startup-breaking edit gate green.
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
