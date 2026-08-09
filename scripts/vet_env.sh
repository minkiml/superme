#!/usr/bin/env bash
# Boot a daemon that serves THIS WORKTREE's code, so verification runs against what was built.
#
# The problem it solves: a check that asks a running server anything asks the daemon on :8787,
# which was started from the main checkout and serves whatever it loaded at boot. A new route reads
# as missing; a DELETED route still reads as present. The second is the dangerous one — vet
# certifies a surface it never looked at.
#
#   eval "$(bash scripts/vet_env.sh start)"     # boots on a free port, exports SUPERME_DAEMON_URL
#   bash scripts/check_fast.sh                  # parity now asks THIS worktree's daemon
#   bash scripts/vet_env.sh stop                # kills it — the files die with the worktree, the
#                                               # process does not
#   bash scripts/vet_env.sh status
#
# stdout carries ONLY the `export` line, so `eval` is safe; everything else goes to stderr.
# Refuses to run in the main checkout: that daemon is the host, and a run is its child.
set -uo pipefail

PY="${SUPERME_PY:-/opt/homebrew/Caskroom/miniconda/base/envs/my-agent/bin/python}"
PORT_LO=8800
PORT_HI=8899
READY_TRIES=80          # × 0.25s ≈ 20s to first response
STOP_TRIES=40           # × 0.25s ≈ 10s for a graceful exit before SIGKILL

say() { echo "$@" >&2; }
die() { say "✗ $*"; exit 1; }

WT="$(git rev-parse --show-toplevel 2>/dev/null)" || die "not inside a git repository"
# --git-common-dir points at the MAIN checkout's .git from anywhere, including a linked worktree.
MAIN="$(cd "$(git rev-parse --git-common-dir)/.." 2>/dev/null && pwd)" || die "cannot locate the main checkout"
STATE="$WT/.vet-env.json"
LOG="$WT/.vet-env.log"

alive() { [ -n "${1:-}" ] && [ "${1:-0}" -gt 0 ] 2>/dev/null && kill -0 "$1" 2>/dev/null; }
listeners() { lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null; }

state_field() {  # state_field <key>  → value, or empty
    [ -f "$STATE" ] || return 0
    "$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get(sys.argv[2],''))" \
        "$STATE" "$1" 2>/dev/null
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

# Spawn the daemon and print its pid. Done from PYTHON, not `nohup … &`, for two reasons a shell
# background job gets wrong:
#   • `( … & echo $! )` reports the SUBSHELL's pid, while the daemon is its child and uvicorn's
#     server is a child of that again. Killing the recorded pid then leaves a daemon holding the
#     port — the exact leak this script exists to prevent.
#   • a shell job inherits the caller's stdout, so `eval "$(… start)"` blocks forever waiting for
#     the command substitution's pipe to close.
# start_new_session makes the pid a PROCESS GROUP leader, so `stop` can take the whole tree; stdio
# is bound to the log and /dev/null, and Popen closes every other descriptor.
spawn() {  # spawn <port> → pid
    "$PY" - "$WT" "$1" "$LOG" "$PY" <<'EOF'
import os, subprocess, sys
wt, port, logpath, py = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
env = dict(os.environ, SUPERME_DAEMON_PORT=port, PYTHONPATH=wt)
with open(logpath, "ab", buffering=0) as log, open(os.devnull, "rb") as null:
    p = subprocess.Popen([py, "-m", "superme_agent.daemon"], cwd=wt, env=env,
                         stdin=null, stdout=log, stderr=log, start_new_session=True)
print(p.pid)
EOF
}

cmd_start() {
    [ "$WT" != "$MAIN" ] || die "this is the main checkout — its daemon is the host, and killing or
   restarting it would kill the run asking for this. Run from an item's worktree."

    local pid port
    pid="$(state_field pid)"; port="$(state_field port)"
    if alive "$pid"; then
        say "▸ already up (pid $pid, port $port)"
        echo "export SUPERME_DAEMON_URL=http://127.0.0.1:$port"
        return 0
    fi
    [ -f "$STATE" ] && rm -f "$STATE"          # stale: the process is gone

    # `.env` is gitignored, so a fresh worktree has none — and without it there is no auth token
    # and not one agent will run. Symlink rather than copy: secrets get exactly one home on disk.
    if [ ! -e "$WT/.env" ] && [ -f "$MAIN/.env" ]; then
        ln -s "$MAIN/.env" "$WT/.env" || die "could not link .env"
        say "▸ linked .env from the main checkout"
    fi

    port="$(free_port)"
    [ -n "$port" ] || die "no free port in $PORT_LO-$PORT_HI"

    # cwd + PYTHONPATH both point at the worktree so `superme_agent` resolves HERE. The DB paths
    # follow the code (APP_DIR is __file__-derived) and are gitignored, so this daemon creates its
    # own empty .system.db/.dev.db in the worktree and cannot touch the live ones. The exported
    # SUPERME_DAEMON_PORT beats the linked `.env` (which sets 8787): python-dotenv does not
    # override variables already in the environment.
    pid="$(spawn "$port")"
    [ -n "$pid" ] || die "could not spawn the daemon"

    local i=0
    while [ $i -lt $READY_TRIES ]; do
        if curl -sf -o /dev/null "http://127.0.0.1:$port/openapi.json"; then
            "$PY" -c "import json,sys;json.dump({'pid':int(sys.argv[1]),'port':int(sys.argv[2]),
                      'url':'http://127.0.0.1:'+sys.argv[2]},open(sys.argv[3],'w'))" \
                  "$pid" "$port" "$STATE"
            say "▸ vet env up on $port (pid $pid) — log: $LOG"
            echo "export SUPERME_DAEMON_URL=http://127.0.0.1:$port"
            return 0
        fi
        alive "$pid" || break
        sleep 0.25; i=$((i + 1))
    done

    say "✗ daemon did not come up on $port — last 20 log lines:"
    tail -20 "$LOG" >&2 2>/dev/null
    alive "$pid" && kill -- -"$pid" 2>/dev/null
    exit 1
}

cmd_stop() {
    local pid port; pid="$(state_field pid)"; port="$(state_field port)"
    rm -f "$STATE"
    if ! alive "$pid" && [ -z "$(listeners "${port:-0}")" ]; then
        say "▸ nothing running"
        return 0
    fi
    # Signal the whole GROUP: uvicorn runs its server in a child, and killing only the leader
    # leaves that child holding the port.
    kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null
    local i=0
    while alive "$pid" && [ $i -lt $STOP_TRIES ]; do sleep 0.25; i=$((i + 1)); done
    alive "$pid" && { say "▸ did not exit — SIGKILL"; kill -9 -- -"$pid" 2>/dev/null; sleep 0.5; }

    # The port is the real test, not the pid: verify nothing still listens, and take whatever does.
    local left; left="$(listeners "${port:-0}")"
    if [ -n "$left" ]; then
        say "▸ port $port still held by: $left — killing"
        for p in $left; do kill -9 "$p" 2>/dev/null; done
        sleep 0.5
        [ -z "$(listeners "$port")" ] || die "port $port is still held after SIGKILL: $(listeners "$port")"
    fi
    say "▸ stopped (pid $pid, port $port released)"
}

cmd_status() {
    local pid port; pid="$(state_field pid)"; port="$(state_field port)"
    if alive "$pid"; then say "▸ up — pid $pid, port $port, log $LOG"
    elif [ -f "$STATE" ]; then say "▸ stale state (pid $pid is gone) — 'start' will clean it up"
    else say "▸ down"; fi
}

case "${1:-}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    *)      die "usage: $0 start|stop|status" ;;
esac
