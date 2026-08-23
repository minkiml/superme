"""Run the SuperMe stack: daemon, web BFF and frontend dev server.

Ports come from the repo-root .env. Ctrl-C stops all three, and one child dying takes the
others with it, so nothing is left half-running.

    python run_superme.py
"""

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from shutil import which

from superme_agent.stdio import utf8_streams

utf8_streams()

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "web" / "frontend"


def load_env() -> None:
    """Read the repo-root .env into os.environ without a dependency. Real values win."""
    env = ROOT / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def wait_for(url: str, proc: subprocess.Popen, seconds: int = 40) -> bool:
    """Poll until the daemon answers, so the BFF never starts against a dead port."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def npm_argv() -> list[str] | None:
    """How to launch npm here, or None when it is not installed.

    On Windows npm is a `.cmd`, which only a shell can run."""
    exe = which("npm")
    if not exe:
        return None
    if os.name == "nt" and os.path.splitext(exe)[1].lower() in (".cmd", ".bat"):
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", exe]
    return [exe]


def _interrupt(*_args) -> None:
    raise KeyboardInterrupt


def take_signals() -> None:
    """Route a kill through the same exit as Ctrl-C, so no child outlives the parent."""
    signal.signal(signal.SIGINT, signal.default_int_handler)
    signal.signal(signal.SIGTERM, _interrupt)


def main() -> int:
    take_signals()
    load_env()
    daemon_port = os.environ.get("SUPERME_DAEMON_PORT", "8787")
    bff_port = os.environ.get("SUPERME_BFF_PORT", "8000")
    fe_port = os.environ.get("VITE_PORT", "5173")

    npm = npm_argv()
    if not npm:
        print("npm is not on PATH — install Node.js, then npm install --prefix web/frontend")
        return 1
    if not (FRONTEND / "node_modules").is_dir():
        print("frontend dependencies are missing — run: npm install --prefix web/frontend")
        return 1

    children: list[tuple[str, subprocess.Popen]] = []

    # Windows defaults to cp1252 and everything here is UTF-8. Set on the children, not this
    # process.
    env = dict(os.environ, PYTHONUTF8="1")

    def start(label: str, argv: list[str], cwd: Path) -> subprocess.Popen:
        print(f"> {label}")
        proc = subprocess.Popen(argv, cwd=str(cwd), env=env)
        children.append((label, proc))
        return proc

    try:
        daemon = start(f"core daemon (:{daemon_port})",
                       [sys.executable, "-m", "superme_agent.daemon"], ROOT)
        if not wait_for(f"http://127.0.0.1:{daemon_port}/openapi.json", daemon):
            print("the daemon did not come up — nothing else started")
            return 1

        start(f"web BFF (:{bff_port})", [sys.executable, "-m", "web.bff"], ROOT)
        start(f"frontend (:{fe_port})", [*npm, "run", "dev"], FRONTEND)
        print(f"\nSuperMe is at http://localhost:{fe_port}   (Ctrl-C stops everything)\n")

        while True:
            for label, proc in children:
                if proc.poll() is not None:
                    print(f"\n{label} exited ({proc.returncode}) — stopping the rest")
                    return proc.returncode or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping…")
        return 0
    finally:
        for _, proc in reversed(children):
            if proc.poll() is None:
                proc.terminate()
        for _, proc in reversed(children):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
