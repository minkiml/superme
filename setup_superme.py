"""Configure a SuperMe install: the local state a checkout does not carry.

Prerequisites are yours — a Python environment with requirements.txt installed and npm
install run. This installs nothing; it writes config and reports what is still missing.

    python setup_superme.py            # configure, then report
    python setup_superme.py --check    # report only, write nothing
"""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "superme_agent"
CHECK = "--check" in sys.argv[1:]

OK, WARN, BAD = "ok", "warn", "missing"
_MARK = {OK: "+", WARN: "~", BAD: "!"}
report: list[tuple[str, str, str]] = []


def note(state: str, title: str, detail: str = "") -> None:
    report.append((state, title, detail))
    print(f" {_MARK[state]} {title}" + (f"\n     {detail}" if detail else ""))


def copy_template(target: Path, template: Path, what: str) -> None:
    """Put a template in place once. An existing file is the user's and is never touched."""
    if target.exists():
        note(OK, f"{what} present", str(target.relative_to(ROOT)))
    elif not template.is_file():
        note(BAD, f"{what} missing", f"no template at {template.relative_to(ROOT)}")
    elif CHECK:
        note(BAD, f"{what} missing", f"would be copied from {template.name}")
    else:
        target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        note(OK, f"{what} created", f"copied from {template.name}")


def main() -> int:
    print(f"\nSuperMe setup{' (check only)' if CHECK else ''}")
    print(f"  repo        {ROOT}")
    print(f"  interpreter {sys.executable}\n")

    print("Prerequisites")
    if sys.version_info < (3, 11):
        note(BAD, "Python 3.11+", f"this is {sys.version.split()[0]}")
    else:
        note(OK, f"Python {sys.version.split()[0]}")

    missing = [m for m in ("yaml", "fastapi", "uvicorn", "websockets", "dotenv", "aiohttp",
                           "claude_agent_sdk") if not _importable(m)]
    if missing:
        note(BAD, "Python packages", f"missing {', '.join(missing)} — pip install -r requirements.txt")
    else:
        note(OK, "Python packages")

    note(OK if shutil.which("npm") else BAD, "npm on PATH",
         "" if shutil.which("npm") else "install Node.js")
    fe_modules = ROOT / "web" / "frontend" / "node_modules"
    note(OK if fe_modules.is_dir() else BAD, "frontend dependencies",
         "" if fe_modules.is_dir() else "run: npm install --prefix web/frontend")
    note(OK if shutil.which("claude") else BAD, "Claude CLI on PATH",
         "" if shutil.which("claude") else "the agent SDK drives it; install Claude Code")

    print("\nSuperMe config")
    copy_template(ROOT / ".env", ROOT / ".env.example", ".env")
    copy_template(APP / "config" / "repos.yaml", APP / "config" / "repos.example.yaml",
                  "repo registry")
    _auth()
    _knowledge_home()
    if not missing:
        _stores()

    return _summary()


def _importable(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _auth() -> None:
    """Report only whether a credential is set. Its value is never read back or printed."""
    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8") if env.is_file() else ""
    filled = {
        line.split("=", 1)[0].strip()
        for line in text.splitlines()
        if not line.lstrip().startswith("#") and "=" in line and line.split("=", 1)[1].strip()
    }
    keys = {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"}
    if keys & filled or any(os.environ.get(k) for k in keys):
        note(OK, "auth credential set")
    else:
        note(BAD, "auth credential missing",
             "put a token from `claude setup-token` in .env as CLAUDE_CODE_OAUTH_TOKEN")


def _knowledge_home() -> None:
    home = Path(os.environ.get("SUPERME_KNOWLEDGE_REPO") or (ROOT / "superme-knowledge"))
    if home.is_dir():
        note(OK, "knowledge home present", str(home))
    elif CHECK:
        note(WARN, "knowledge home absent", f"would be created at {home}")
    else:
        home.mkdir(parents=True, exist_ok=True)
        note(OK, "knowledge home created", str(home))


def _stores() -> None:
    """Open the spine and the dev store once, which is what creates their schemas."""
    if CHECK:
        note(WARN, "local databases", "skipped — --check writes nothing")
        return
    sys.path.insert(0, str(ROOT))
    try:
        from superme_agent.core.dev_store import DevStore
        from superme_agent.core.spine import SystemSpine
        from superme_agent.paths import DEV_DB_FILE

        spine = SystemSpine()
        DevStore(DEV_DB_FILE)
        hub = spine.repos().get("global")
        note(OK, "local databases ready")
        if hub:
            note(OK, "hub resolves", f"'{hub.label}' at {hub.cwd}")
        else:
            note(WARN, "no hub in the registry", "add a `global` entry to config/repos.yaml")
    except Exception as e:
        note(BAD, "could not open the local databases", f"{type(e).__name__}: {e}")


def _summary() -> int:
    bad = [t for s, t, _ in report if s == BAD]
    print()
    if bad:
        print(f"NOT READY — {len(bad)} to fix: " + ", ".join(bad))
        return 1
    print("READY. Start the stack with:\n")
    print("    python -m superme_agent.daemon        # core daemon")
    print("    python -m web.bff                     # web BFF")
    print("    npm run dev --prefix web/frontend     # frontend\n")
    print(f"Then open http://localhost:{os.environ.get('VITE_PORT', '5173')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
