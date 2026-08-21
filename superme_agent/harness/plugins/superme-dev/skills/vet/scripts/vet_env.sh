#!/usr/bin/env bash
# Boot a server that runs THIS WORKTREE's code, so verification runs against what was built.
#
# Without it, a check asks whatever instance is already listening, serving the code IT loaded.
# A new endpoint reads as missing and a deleted one still reads as present — a pass for a
# surface nobody looked at.
#
#   eval "$(bash "$SCRIPT" start)"     # boots on a free port, exports the repo's URL variable
#   …run the checks…
#   bash "$SCRIPT" stop                # ALWAYS, including after a failure
#   bash "$SCRIPT" status
#
# WHAT to boot comes from the repo's `vet_env` block; this script owns only the lifecycle. A
# repo without one has no vet env, so a check that needs it is unrunnable rather than misaimed.
#
# stdout carries ONLY the `export` line, so `eval` is safe; everything else goes to stderr.
# Refuses to run outside a worktree: the main checkout's server is the host, and a run is its child.
set -uo pipefail

# The interpreter to run this repo's Python with: `SUPERME_PY` pins one, otherwise whatever
# the active environment puts on PATH. Never a hardcoded path — that is one machine's answer.
PY="${SUPERME_PY:-}"
if [ -z "$PY" ]; then
    for c in python3 python; do
        command -v "$c" >/dev/null 2>&1 && { PY="$c"; break; }
    done
fi
[ -n "$PY" ] || { echo "no python on PATH — set SUPERME_PY to an interpreter" >&2; exit 1; }
PORT_LO="${VET_ENV_PORT_LO:-8800}"
PORT_HI="${VET_ENV_PORT_HI:-8899}"
READY_TRIES=80          # × 0.25s ≈ 20s to first response
STOP_TRIES=40           # × 0.25s ≈ 10s for a graceful exit before SIGKILL

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Six levels up from this script is the package root.
INSTALL="$(cd "$HERE/../../../../../.." && pwd)"

say() { echo "$@" >&2; }
die() { say "✗ $*"; exit 1; }

WT="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git repository"
MAIN="$(cd "$(git rev-parse --git-common-dir)/.." 2>/dev/null && pwd)" || die "cannot locate the main checkout"
STATE="$WT/.vet-env.json"
LOG="$WT/.vet-env.log"
# Worktrees live at <worktrees-home>/<repo-id>/<item-id>, so the repo id is the parent dir's name.
# An explicit argument wins, for a worktree somewhere else.
REPO_ID="${2:-$(basename "$(dirname "$WT")")}"

# --- identity by cwd, never by a remembered pid ------------------------------------------------
# A daemon belongs to this worktree if its working directory IS this worktree. That survives a
# lost state file and a second daemon on another port; the state file is only a convenience.
daemons_here() {
    local p cwd
    : "${MATCH:=$(cfg cmd | tr ' ' '\n' | grep -v '^{' | tail -1)}"
    for p in $(lsof -nP -iTCP -sTCP:LISTEN -t 2>/dev/null | sort -u); do
        cwd="$(lsof -a -p "$p" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | tail -1)"
        # NEVER the host. A process in the MAIN checkout is the daemon every run is a child of,
        # and no path here may adopt, stop, or count it. Restated where the pids are chosen: a
        # rule that lives only at the entrance is one call site from being bypassed.
        [ "$cwd" = "$MAIN" ] && continue
        [ "$cwd" = "$WT" ] || continue
        # …AND it must be the thing we boot. cwd alone also matches the agent's own process
        # tree, so a `stop` would SIGTERM the caller. Two facts, not one.
        [ -z "$MATCH" ] && { echo "$p"; continue; }
        ps -o command= -p "$p" 2>/dev/null | grep -qF -- "$MATCH" && echo "$p"
    done
}

port_of() {  # port_of <pid> → the first TCP port it listens on
    lsof -nP -iTCP -sTCP:LISTEN -a -p "$1" -Fn 2>/dev/null | sed -n 's/^n.*:\([0-9]*\)$/\1/p' | head -1
}

alive() { [ -n "${1:-}" ] && [ "${1:-0}" -gt 0 ] 2>/dev/null && kill -0 "$1" 2>/dev/null; }

# --- the repo's vet_env block ------------------------------------------------------------------
cfg() {  # cfg <key> → value from repos.yaml, empty when absent
    "$PY" - "$INSTALL/config/repos.yaml" "$REPO_ID" "$1" <<'EOF'
import sys, yaml
path, repo, key = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    repos = (yaml.safe_load(open(path)) or {}).get("repos") or {}
except Exception:
    sys.exit(0)
print(((repos.get(repo) or {}).get("vet_env") or {}).get(key) or "")
EOF
}

free_port() {
    "$PY" - "$PORT_LO" "$PORT_HI" <<'EOF'
import socket, sys
lo, hi = int(sys.argv[1]), int(sys.argv[2])
for p in range(lo, hi + 1):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p))
        print(p)
        break
    except OSError:
        continue
    finally:
        s.close()
EOF
}

# Spawned from Python, not `nohup … &`: a shell job reports the subshell in `$!` while the server
# is its grandchild, and it inherits stdout, so `eval "$(… start)"` would never return.
spawn() {  # spawn <cmd> <port> <port_env> → pid
    "$PY" - "$WT" "$1" "$2" "$3" "$LOG" <<'EOF'
import os, shlex, subprocess, sys
wt, cmd, port, port_env, logpath = sys.argv[1:6]
env = dict(os.environ, PYTHONPATH=wt)
if port_env:
    env[port_env] = port
# `{py}` is this interpreter, so a repo's `cmd` names its runtime without pinning one machine's path.
argv = shlex.split(cmd.replace("{port}", port).replace("{py}", sys.executable))
with open(logpath, "ab", buffering=0) as log, open(os.devnull, "rb") as null:
    p = subprocess.Popen(argv, cwd=wt, env=env,
                         stdin=null, stdout=log, stderr=log, start_new_session=True)
print(p.pid)
EOF
}

write_state() {  # write_state <pid> <port>
    "$PY" -c "import json,sys;json.dump({'pid':int(sys.argv[1]),'port':int(sys.argv[2])},
              open(sys.argv[3],'w'))" "$1" "$2" "$3" 2>/dev/null || true
}

cmd_start() {
    local url_env cmd port_env ready pid port
    url_env="$(cfg url_env)"; cmd="$(cfg cmd)"
    port_env="$(cfg port_env)"; ready="$(cfg ready)"
    [ -n "$cmd" ] || die "repo '$REPO_ID' declares no vet_env in config/repos.yaml — there is no
   server to boot here. A check that needs one is unrunnable in this repo; report it as such
   rather than pointing it at an instance you did not start."
    [ -n "$url_env" ] || url_env="VET_ENV_URL"

    # ADOPT rather than stack. Without this, a lost state file makes `start` spawn a second server
    # while the first still holds a port — and nothing afterwards knows the first exists.
    pid="$(daemons_here | head -1)"
    if [ -n "$pid" ]; then
        port="$(port_of "$pid")"
        write_state "$pid" "$port" "$STATE"
        say "▸ adopted the server already running for this worktree (pid $pid, port $port)"
        echo "export $url_env=http://127.0.0.1:$port"
        return 0
    fi

    # A worktree has no .env, so a server needing credentials would start but not work.
    # Symlink, never copy: secrets get one home on disk.
    if [ ! -e "$WT/.env" ] && [ -f "$MAIN/.env" ]; then
        ln -s "$MAIN/.env" "$WT/.env" && say "▸ linked .env from the main checkout"
    fi

    port="$(free_port)"
    [ -n "$port" ] || die "no free port in $PORT_LO-$PORT_HI"
    pid="$(spawn "$cmd" "$port" "$port_env")"
    [ -n "$pid" ] || die "could not spawn: $cmd"

    local i=0
    while [ $i -lt $READY_TRIES ]; do
        if curl -sf -o /dev/null "http://127.0.0.1:$port${ready:-/}"; then
            write_state "$pid" "$port" "$STATE"
            say "▸ vet env up on $port (pid $pid) — log: $LOG"
            echo "export $url_env=http://127.0.0.1:$port"
            return 0
        fi
        alive "$pid" || break
        sleep 0.25; i=$((i + 1))
    done

    say "✗ '$cmd' did not answer on $port — last 20 log lines:"
    tail -20 "$LOG" >&2 2>/dev/null
    alive "$pid" && kill -9 "$pid" 2>/dev/null
    exit 1
}

cmd_stop() {
    local pids p
    pids="$(daemons_here)"
    if [ -z "$pids" ]; then
        rm -f "$STATE"
        say "▸ nothing running for this worktree"
        return 0
    fi
    # Signal the GROUP: a server may run its worker in a child, and killing only the leader leaves
    # that child holding the port.
    for p in $pids; do kill "$p" 2>/dev/null; done
    local i=0
    while [ -n "$(daemons_here)" ] && [ $i -lt $STOP_TRIES ]; do sleep 0.25; i=$((i + 1)); done
    for p in $(daemons_here); do say "▸ pid $p did not exit — SIGKILL"; kill -9 "$p" 2>/dev/null; done
    sleep 0.5
    local left; left="$(daemons_here)"
    [ -z "$left" ] || die "still listening for this worktree after SIGKILL: $left"
    rm -f "$STATE"
    say "▸ stopped:$(printf ' %s' $pids)"
}

cmd_status() {
    local pids p
    pids="$(daemons_here)"
    if [ -z "$pids" ]; then say "▸ down"; return 0; fi
    for p in $pids; do say "▸ up — pid $p, port $(port_of "$p"), log $LOG"; done
}

# EVERY subcommand refuses here, not just `start`: `stop` sweeps by cwd, and the host daemon's
# cwd IS the main checkout. A guard on one verb is not a guard — this script belongs to worktrees.
[ "$WT" != "$MAIN" ] || die "this is the main checkout, not an item worktree. The server running
   here is the HOST — every run is its child, so starting, stopping or sweeping it would kill the
   run asking. Run this from an item's worktree; there is no vet env to manage here."

case "${1:-}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    *)      die "usage: $0 start|stop|status [repo-id]" ;;
esac
