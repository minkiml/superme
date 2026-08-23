#!/usr/bin/env bash
# Boot a server that runs THIS WORKTREE's code, so verification runs against what was built.
#
#   eval "$(bash "$SCRIPT" start)"     # boots on a free port, exports the repo's URL variable
#   …run the checks…
#   bash "$SCRIPT" stop                # ALWAYS, including after a failure
#   bash "$SCRIPT" status
#
# The lifecycle lives in vet_env.py, which runs the same on every OS. This wrapper exists so the
# `eval` above still has a shell to hand the export line to, and finds the interpreter to use.
set -uo pipefail

# `SUPERME_PY` pins one, otherwise whatever the active environment puts on PATH. Never a
# hardcoded path — that is one machine's answer.
PY="${SUPERME_PY:-}"
if [ -z "$PY" ]; then
    for c in python3 python; do
        command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
    done
fi
[ -n "$PY" ] || { echo "no python on PATH — set SUPERME_PY to an interpreter" >&2; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Git Bash reports `/c/Users/…`, which a native Windows python cannot open. `cygpath` ships with
# it and exists nowhere else, so its presence is the test.
command -v cygpath >/dev/null 2>&1 && HERE="$(cygpath -w "$HERE")"
exec "$PY" "$HERE/vet_env.py" "$@"
