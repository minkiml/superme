#!/usr/bin/env bash
# Fast, read-only gate to run between edits. Seconds, and it mutates nothing.
#
#   bash scripts/check_fast.sh                 # routes + surface + layers + encodings + tools + tsc
#   STRICT=1 bash scripts/check_fast.sh        # also fail on ANY OpenAPI shape drift
#
# Every check reads the code on disk, never a running daemon: one serving older code would
# certify a surface this tree cannot produce. For a live daemon, run `parity check --live`.
#
# The heavier E2E suites in scripts/test_*.py run only when a change reaches them.
set -uo pipefail
cd "$(dirname "$0")/.."

# The interpreter to run this repo's Python with: `SUPERME_PY` pins one, otherwise whatever
# the active environment puts on PATH. Never a hardcoded path — that is one machine's answer.
PY="${SUPERME_PY:-}"
if [ -z "$PY" ]; then
    for c in python3 python; do
        command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
    done
fi
[ -n "$PY" ] || { echo "no python on PATH — set SUPERME_PY to an interpreter" >&2; exit 1; }
EXTRA=""; [ "${STRICT:-0}" = "1" ] && EXTRA="--strict-shapes"

echo "▸ import check (fresh app load)"
PYTHONPATH=. "$PY" -c "from superme_agent.daemon import server; assert server.app" ; IMPORT=$?

echo "▸ parity check ${EXTRA}"
PYTHONPATH=. "$PY" -m scripts.parity check $EXTRA; PARITY=$?

echo "▸ import surface"
PYTHONPATH=. "$PY" -m scripts.api_snapshot check; API=$?

echo "▸ import layering"
PYTHONPATH=. "$PY" -m scripts.layers; LAYERS=$?

echo "▸ declared encodings"
PYTHONPATH=. "$PY" -m scripts.encoding_gate; ENC=$?

echo "▸ tool descriptions"
PYTHONPATH=. "$PY" -m scripts.tool_descriptions >/dev/null; DESC=$?
[ $DESC -ne 0 ] && PYTHONPATH=. "$PY" -m scripts.tool_descriptions | tail -n +2

echo "▸ frontend typecheck"
( cd web/frontend && npx -y tsc --noEmit ); TSC=$?

echo "————"
if [ $IMPORT -eq 0 ] && [ $PARITY -eq 0 ] && [ $API -eq 0 ] && [ $LAYERS -eq 0 ] && [ $ENC -eq 0 ] && [ $DESC -eq 0 ] && [ $TSC -eq 0 ]; then
  echo "✓ FAST GATE GREEN"; exit 0
else
  echo "✗ FAST GATE RED  (import=$IMPORT parity=$PARITY api=$API layers=$LAYERS enc=$ENC desc=$DESC tsc=$TSC)"; exit 1
fi
