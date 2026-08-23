"""Configure a SuperMe install: the local state a checkout does not carry.

Re-running is safe.

    python setup_superme.py             # write what is missing
    python setup_superme.py --check     # report only
    python setup_superme.py --seed-hub  # refresh the shipped hub docs from the live ones
"""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "superme_agent"
CHECK = "--check" in sys.argv[1:]
SEED_HUB = "--seed-hub" in sys.argv[1:]

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
        _hub_knowledge()

    return _summary()


def _importable(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _auth() -> None:
    """Report whether SuperMe can authenticate — either credential counts. Never reads a value back."""
    sys.path.insert(0, str(ROOT))
    try:
        from superme_agent.core.auth import auth_status
    except ImportError:
        note(WARN, "auth not checked", "install the Python packages first")
        return
    status = auth_status(refresh=True)
    note(OK if status["ready"] else BAD,
         "auth ready" if status["ready"] else "no credential", status["detail"])

    env = ROOT / ".env"
    text = env.read_text(encoding="utf-8") if env.is_file() else ""
    if any(line.split("=", 1)[0].strip() == "ANTHROPIC_API_KEY" and line.split("=", 1)[1].strip()
           for line in text.splitlines()
           if not line.lstrip().startswith("#") and "=" in line):
        note(WARN, "ANTHROPIC_API_KEY is set and will be ignored",
             "SuperMe runs on Claude plan auth only; the key is dropped from the process")


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


def _hub_knowledge() -> None:
    """Put the shipped hub anchor docs in place once, so a guest's hub is readable on arrival.

    Nobody can say what SuperMe is for on the day they clone it, so this repo's memory ships."""
    from superme_agent.gateway import contexts
    from superme_agent.paths import HUB_KNOWLEDGE_SEED_DIR

    seed = HUB_KNOWLEDGE_SEED_DIR
    try:
        live = contexts.resolve("global", "dev").internal_root / "dev" / "general"
    except Exception as e:  # noqa: BLE001 — no hub in the registry is already reported above
        note(WARN, "hub knowledge skipped", f"{type(e).__name__}: {e}")
        return
    # Refreshing runs FROM the live docs, so an empty seed is the ordinary case — test it first.
    if SEED_HUB:
        _refresh_seed(live, seed)
        return
    docs = [p for p in seed.glob("*.md") if p.name != "README.md"] if seed.is_dir() else []
    if not docs:
        note(WARN, "no hub docs to install", f"{seed.relative_to(ROOT)} holds no anchor docs")
        return
    if live.is_dir() and any(live.glob("*.md")):
        note(OK, "hub knowledge present", "yours now — setup never overwrites it")
        return
    if CHECK:
        note(WARN, "hub knowledge absent", f"would be copied to {live}")
        return
    shutil.copytree(seed, live, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("README.md"))
    note(OK, "hub knowledge installed", f"{len(docs)} anchor docs → {live}")


def _refresh_seed(live: Path, seed: Path) -> None:
    """`--seed-hub`: copy the LIVE hub docs back over the shipped ones. The owner's copy is the
    source; this is how it reaches the next guest."""
    if not (live.is_dir() and any(live.glob("*.md"))):
        note(BAD, "nothing to seed from", f"no anchor docs at {live}")
        return
    for stale in seed.glob("*.md"):
        if stale.name != "README.md":
            stale.unlink()
    shutil.copytree(live, seed, dirs_exist_ok=True)
    note(OK, "shipped hub docs refreshed",
         f"{len(list(seed.glob('*.md'))) - 1} docs ← {live}")


def _summary() -> int:
    bad = [t for s, t, _ in report if s == BAD]
    print()
    if bad:
        print(f"NOT READY — {len(bad)} to fix: " + ", ".join(bad))
        return 1
    print("READY. Start the stack with:\n")
    print("    python run_superme.py\n")
    print(f"Then open http://localhost:{os.environ.get('VITE_PORT', '5173')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
