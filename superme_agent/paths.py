"""The code, the stores, the harness and the knowledge home, plus the one `load_dotenv`
call — loading `.env` is a side effect the whole process depends on. APP_DIR is
code-anchored, so it never moves with the agent's cwd.
"""

import os
import logging
from pathlib import Path

from dotenv import load_dotenv

from .stdio import utf8_streams

utf8_streams()

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent                               # repo root = the default workspace cwd

# One repo-root `.env` for every surface. Must precede any `os.environ` read below.
ENV_FILE = ROOT_DIR / ".env"
load_dotenv(ENV_FILE)

# An API key silently outranks the OAuth token and bills pay-as-you-go.
os.environ.pop("ANTHROPIC_API_KEY", None)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("superme-agent")

HARNESS_DIR = APP_DIR / "harness"
SELF_FILE = HARNESS_DIR / "SELF.md"
# SELF.md says WHO in every mode; the charter says WHAT MODE. Picked by `Context.mode`.
# Keyed by the frame a turn operates in. Usually the mode, but a deputy judges rather than
# develops, so it selects its own.
CHARTER_FILES = {
    "core": HARNESS_DIR / "core-charter.md",
    "dev": HARNESS_DIR / "dev-charter.md",
    "deputy": HARNESS_DIR / "deputy-charter.md",
}
# Skill discovery is flat, so modes are separated by plugin, not by folder.
PLUGINS_DIR = HARNESS_DIR / "plugins"
SHARED_PLUGIN_DIR = PLUGINS_DIR / "superme-shared"   # every mode
CORE_PLUGIN_DIR = PLUGINS_DIR / "superme-core"       # mode=core
DEV_PLUGIN_DIR = PLUGINS_DIR / "superme-dev"         # mode=dev
# Learned, always-on behavior, one file per item. SELF.md and the charters are
# hand-authored and never auto-written.
CONSTITUTION_DIR = HARNESS_DIR / "constitution"

# A session's cwd is the operated repo, so a charter path into the plugin must be absolute.
CHARTER_PATH_TOKENS = {"{DEV_GLOSSARY}": DEV_PLUGIN_DIR / "references" / "glossary.md"}


def fill_charter_paths(text: str) -> str:
    """Charter text with each `{TOKEN}` replaced by the install path it names."""
    for token, path in CHARTER_PATH_TOKENS.items():
        text = text.replace(token, str(path))
    return text


def plugins_for(mode: str, op_home: Path | None = None) -> list[str]:
    """Plugin paths for a turn: the universal harness, then this repo's own operational cell."""
    mode_dir = DEV_PLUGIN_DIR if mode == "dev" else CORE_PLUGIN_DIR
    paths = [str(SHARED_PLUGIN_DIR), str(mode_dir)]
    if op_home is not None:
        # A bare placeholder dir carries no manifest and has nothing to load.
        if (Path(op_home) / ".claude-plugin" / "plugin.json").is_file():
            paths.append(str(op_home))
    return paths


# Static meta is hand-editable YAML, live status is SQLite; the spine presents one API.
CONFIG_DIR = APP_DIR / "config"
SYSTEM_CONFIG_FILE = CONFIG_DIR / "system.yaml"      # singleton static-meta
REPOS_CONFIG_FILE = CONFIG_DIR / "repos.yaml"        # repo registry, scope-aware
# The registry is local state — the spine writes to it — so the tracked file is a seed.
REPOS_SEED_FILE = CONFIG_DIR / "repos.example.yaml"
# Losing the registry loses every connected repo, and it has no git history to fall back on.
REPOS_BACKUP_DIR = CONFIG_DIR / "repos-backups"
# A guest cannot answer what SuperMe is for, so this repo's memory ships.
HUB_KNOWLEDGE_SEED_DIR = CONFIG_DIR / "hub-knowledge"
SYSTEM_DB_FILE = APP_DIR / ".system.db"              # live: session, run, model_override
# One gitignored knowledge repo holds every connected repo's sub-home, keyed by repo id.
KNOWLEDGE_REPO_DIR = Path(
    os.environ.get("SUPERME_KNOWLEDGE_REPO", str(ROOT_DIR / "superme-knowledge"))
)
# No seed template: a repo's knowledge home is created lazily on first write.

# Per-repo operational skills, keyed by repo id x mode. Operational lives with the code.
LOCAL_HARNESS_DIR = APP_DIR / "local-harness"
# Constitutional items any repo can activate for itself: opt-in by reference, no body copy.
ASSET_DIR = LOCAL_HARNESS_DIR / "asset"
# The SDK reveals the slash-command list only during a turn, so the palette reads the last one.
SLASH_COMMANDS_FILE = APP_DIR / ".slash_commands.json"

# Operational queue/run/status state, keyed by context_id. Durable knowledge stays in
# markdown; this DB is never committed.
DEV_DB_FILE = APP_DIR / ".dev.db"

# Where the Claude Agent SDK stores per-session transcripts, keyed by cwd. Bubble history
# replays from these files.
CLAUDE_PROJECTS_DIR = Path(
    os.environ.get("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects"))
)

# Localhost only, single-owner, no auth.
DAEMON_HOST = os.environ.get("SUPERME_DAEMON_HOST", "127.0.0.1")
DAEMON_PORT = int(os.environ.get("SUPERME_DAEMON_PORT", "8787"))
# How long the daemon waits for a surface to answer an approval before denying (s).
DAEMON_APPROVAL_TIMEOUT = int(os.environ.get("SUPERME_APPROVAL_TIMEOUT", "180"))
