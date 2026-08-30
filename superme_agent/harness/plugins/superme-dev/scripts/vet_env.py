"""Boot a server running this worktree's code, so a check cannot ask one serving another.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Six levels above this script's directory is the package root; the install, never the worktree.
INSTALL = Path(__file__).resolve().parents[6]
# Ahead of PYTHONPATH: the worktree is also a candidate `superme_agent`, and it is the code
# under test.
sys.path.insert(0, str(INSTALL.parent))

from superme_agent.core.git_layer import (  # the path insert above must land first
    VET_LOG, VET_STATE, terminate, pid_alive, servers_in,
)

PORT_LO = int(os.environ.get("VET_ENV_PORT_LO") or 8800)
PORT_HI = int(os.environ.get("VET_ENV_PORT_HI") or 8899)
READY_SECONDS = 20
STOP_SECONDS = 10


def say(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def die(msg: str) -> None:
    say(f"✗ {msg}")
    raise SystemExit(1)


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8")
    return out.stdout.strip() if out.returncode == 0 else ""


def vet_env_config(repo_id: str) -> dict:
    """The repo's `vet_env` block, or {} when it declares none."""
    import yaml
    try:
        repos = (yaml.safe_load((INSTALL / "config" / "repos.yaml").read_text(encoding="utf-8"))
                 or {}).get("repos") or {}
    except (OSError, yaml.YAMLError) as exc:
        die(f"cannot read config/repos.yaml — {exc}")
    return (repos.get(repo_id) or {}).get("vet_env") or {}


def free_port() -> int:
    import socket
    for port in range(PORT_LO, PORT_HI + 1):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    die(f"no free port in {PORT_LO}-{PORT_HI}")
    return 0


def spawn(cmd: str, port: int, port_env: str, wt: Path, log: Path) -> int:
    """Start the server detached, so it outlives the shell that asked for it."""
    env = dict(os.environ, PYTHONPATH=str(wt))
    if port_env:
        env[port_env] = str(port)
    # `{py}` is this interpreter. `posix=False` on Windows, where shlex eats backslashes.
    argv = shlex.split(cmd.replace("{port}", str(port)).replace("{py}", sys.executable),
                       posix=os.name != "nt")
    # Detach: a new session on POSIX, a new process group off the console on Windows, which
    # ignores `start_new_session` entirely.
    detach: dict = {"start_new_session": True}
    if os.name == "nt":
        detach = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008}
    with open(log, "ab", buffering=0) as sink, open(os.devnull, "rb") as null:
        p = subprocess.Popen(argv, cwd=wt, env=env,
                             stdin=null, stdout=sink, stderr=sink, **detach)
    return p.pid


def answers(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status < 400
    except (urllib.error.URLError, OSError, ValueError):
        return False


def link_env(wt: Path, main: Path) -> None:
    """Link the main checkout's .env in, so a server needing credentials can authenticate."""
    if (wt / ".env").exists() or not (main / ".env").is_file():
        return
    try:
        os.symlink(main / ".env", wt / ".env")
        say("▸ linked .env from the main checkout")
        return
    except (OSError, NotImplementedError):
        pass
    try:
        shutil.copy2(main / ".env", wt / ".env")
        say("▸ COPIED .env from the main checkout (this OS refused a symlink) — it goes when the "
            "worktree does, but until then your credentials exist twice")
    except OSError as exc:
        say(f"▸ could not bring .env into the worktree ({exc}) — a server needing it will start "
            f"but fail to authenticate")


def cmd_start(wt: Path, main: Path, cfg: dict, match: str) -> None:
    url_env = cfg.get("url_env") or "VET_ENV_URL"
    ready = cfg.get("ready") or "/"

    # Without this a lost state file spawns a second server while the first still holds a port.
    if running := servers_in(wt, match):
        pid = running[0]
        port = port_of(pid, wt)
        if not port:
            die(f"a server for this worktree is already running (pid {pid}) but its port cannot "
                f"be determined here. Stop it — `vet_env.sh stop` — and start again.")
        write_state(wt, pid, port)
        say(f"▸ adopted the server already running for this worktree (pid {pid}, port {port})")
        print(f"export {url_env}=http://127.0.0.1:{port}")
        return

    link_env(wt, main)
    port = free_port()
    log = wt / VET_LOG
    pid = spawn(cfg["cmd"], port, cfg.get("port_env") or "", wt, log)

    deadline = time.monotonic() + READY_SECONDS
    while time.monotonic() < deadline:
        if answers(f"http://127.0.0.1:{port}{ready}"):
            write_state(wt, pid, port)
            say(f"▸ vet env up on {port} (pid {pid}) — log: {log}")
            print(f"export {url_env}=http://127.0.0.1:{port}")
            return
        if not pid_alive(pid):
            break
        time.sleep(0.25)

    say(f"✗ '{cfg['cmd']}' did not answer on {port} — last 20 log lines:")
    try:
        say("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-20:]))
    except OSError:
        say("(no log)")
    terminate(pid)
    raise SystemExit(1)


def cmd_stop(wt: Path, match: str) -> None:
    pids = servers_in(wt, match)
    if not pids:
        (wt / VET_STATE).unlink(missing_ok=True)
        say("▸ nothing running for this worktree")
        return
    for pid in pids:
        terminate(pid)
    deadline = time.monotonic() + STOP_SECONDS
    while time.monotonic() < deadline and servers_in(wt, match):
        time.sleep(0.25)
    for pid in servers_in(wt, match):
        say(f"▸ pid {pid} did not exit — killing it")
        _kill(pid)
    time.sleep(0.5)
    # Delete the state file last: where there is no `lsof` it is the only handle on this pid.
    if left := servers_in(wt, match):
        die(f"still listening for this worktree after a kill: {left}")
    (wt / VET_STATE).unlink(missing_ok=True)
    say("▸ stopped: " + " ".join(str(p) for p in pids))


def cmd_status(wt: Path, match: str) -> None:
    pids = servers_in(wt, match)
    if not pids:
        say("▸ down")
        return
    for pid in pids:
        say(f"▸ up — pid {pid}, port {port_of(pid, wt) or '?'}, log {wt / VET_LOG}")


def _kill(pid: int) -> None:
    import signal
    try:
        os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    except OSError:
        pass


def port_of(pid: int, wt: Path) -> str:
    """The port this server listens on, asked of the OS first.

    A state file can outlive the run."""
    try:
        r = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-p", str(pid), "-Fn"],
                           capture_output=True, text=True, timeout=10, encoding="utf-8")
        for line in r.stdout.splitlines():
            if line.startswith("n") and ":" in line:
                return line.rsplit(":", 1)[-1]
    except Exception:  # noqa: BLE001 — no lsof, or the process went away; the state file answers
        pass
    try:
        return str(json.loads((wt / VET_STATE).read_text(encoding="utf-8")).get("port") or "")
    except (OSError, ValueError, json.JSONDecodeError):
        return ""


def write_state(wt: Path, pid: int, port: int | str) -> None:
    port = int(port) if str(port).isdigit() else port
    try:
        (wt / VET_STATE).write_text(json.dumps({"pid": pid, "port": port}), encoding="utf-8")
    except OSError:
        pass                                  # a convenience, never the record


def main(argv: list[str]) -> None:
    verb = argv[1] if len(argv) > 1 else ""
    if verb not in ("start", "stop", "status"):
        die(f"usage: {Path(argv[0]).name} start|stop|status [repo-id]")

    top = git("rev-parse", "--show-toplevel")
    common = git("rev-parse", "--git-common-dir")
    if not top or not common:
        die("not inside a git repository")
    wt = Path(top).resolve()
    main_checkout = Path(common).resolve().parent

    # EVERY verb refuses, not just `start`: `stop` sweeps by cwd, and the host daemon's cwd IS
    # the main checkout.
    if wt == main_checkout:
        die("this is the main checkout, not an item worktree. The server running here is the "
            "HOST — every run is its child, so starting, stopping or sweeping it would kill the "
            "run asking. Run this from an item's worktree; there is no vet env to manage here.")

    # Worktrees live at <worktrees-home>/<repo-id>/<item-id>. An explicit argument wins, for
    # a worktree somewhere else.
    repo_id = argv[2] if len(argv) > 2 else wt.parent.name
    cfg = vet_env_config(repo_id)
    if not str(cfg.get("cmd") or "").strip():
        die(f"repo '{repo_id}' declares no vet_env in config/repos.yaml — there is no server to "
            f"boot here. A check that needs one is unrunnable in this repo; report it as such "
            f"rather than pointing it at an instance you did not start.")

    # cwd alone also matches the agent's own process tree, so a `stop` would signal the caller.
    match = ([w for w in str(cfg["cmd"]).split() if not w.startswith("{")] or [""])[-1]

    if verb == "start":
        cmd_start(wt, main_checkout, cfg, match)
    elif verb == "stop":
        cmd_stop(wt, match)
    else:
        cmd_status(wt, match)


if __name__ == "__main__":
    main(sys.argv)
