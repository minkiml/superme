"""Configuration, paths, and shared setup for the SuperMe agent.

Surface-neutral: this module holds paths, .env loading, and logging only. It must NOT
require any surface's credentials (e.g. Slack tokens), so the Core can import it
without a Slack environment. Slack-specific config lives in the Slack adapter
(runtime/slack_app.py, runtime/permissions.py).

Path model (the host-app vs workspace split):
  APP_DIR   = the host application itself (this package). Holds .sessions.json, runtime
              state, the harness, and config. Anchored to the CODE location, so it never
              moves when the agent roams into a different workspace.
  ROOT_DIR  = the repo root = the DEFAULT workspace cwd (and future global harness).
              Holds the single .env (shared by all surfaces; see .env.example).
"""

import os
import logging
from pathlib import Path

from dotenv import load_dotenv

# config.py lives at superme_agent/runtime/config.py
APP_DIR = Path(__file__).resolve().parent.parent       # superme_agent/
ROOT_DIR = APP_DIR.parent                               # repo root = default workspace

# .env loading (must precede any os.environ reads below): ONE repo-root .env is the single
# source of truth for ALL surfaces (Backend, Frontend `VITE_*`, Shared). See .env.example.
ENV_FILE = ROOT_DIR / ".env"
load_dotenv(ENV_FILE)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("superme-agent")

# --- Paths (code-anchored) ------------------------------------------------------
HARNESS_DIR = APP_DIR / "harness"
PERSONA_FILE = HARNESS_DIR / "persona.md"
PLUGIN_DIR = HARNESS_DIR / "plugin"
REGISTRY_FILE = APP_DIR / "config" / "registry.yaml"
# Global SuperMe knowledge home (Repo 2): gitignored from the public app repo, its own
# private repo. Minimal base structure for now; the real taxonomy is a future design job.
KNOWLEDGE_GLOBAL_DIR = Path(
    os.environ.get("SUPERME_KNOWLEDGE_GLOBAL", str(ROOT_DIR / "superme-global-knowledge"))
)
SESSIONS_FILE = APP_DIR / ".sessions.json"
# Channel→workspace state {channel: {workspace, locked}}, managed live from Slack
# (not committed; per-environment).
CHANNELS_FILE = APP_DIR / ".channel_workspaces.json"
# Channel→model override {channel: "haiku"|"sonnet"|"opus"}, set live from Slack
# (`@bot /model …`). Unset channels use the CLI default model.
MODELS_FILE = APP_DIR / ".channel_models.json"

# --- Shared command layer + "/" palette (1.5) -----------------------------------
# Per-context model override (set via /model; both web and Slack share this layer).
CONTEXT_MODELS_FILE = APP_DIR / ".context_models.json"
# Cached slash-command lists per context, so the web "/" palette is available on
# connect (the SDK only reveals the list during a turn, so we remember the last one).
SLASH_COMMANDS_FILE = APP_DIR / ".slash_commands.json"

# --- Sessions (Stage B / 1.5) ---------------------------------------------------
# `.sessions.json` (SESSIONS_FILE above) is the single cross-surface session log,
# keyed by workspace (cwd) — see core/session_index.py. Both Slack and the web write it.
# Where the Claude Agent SDK / CLI stores per-session transcript JSONL (keyed by cwd).
# Bubble history is replayed from these files — the SDK's own source of truth.
CLAUDE_PROJECTS_DIR = Path(
    os.environ.get("CLAUDE_PROJECTS_DIR", str(Path.home() / ".claude" / "projects"))
)

# --- SuperMe Core daemon (Stage B): localhost only, single-owner, no auth -------
DAEMON_HOST = os.environ.get("SUPERME_DAEMON_HOST", "127.0.0.1")
DAEMON_PORT = int(os.environ.get("SUPERME_DAEMON_PORT", "8787"))
# How long the daemon waits for a surface to answer an approval before denying (s).
DAEMON_APPROVAL_TIMEOUT = int(os.environ.get("SUPERME_APPROVAL_TIMEOUT", "180"))


def warn_on_conflicting_auth() -> None:
    """Subscription auth (Claude Max) wins only if no API key shadows it."""
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        log.warning(
            "Both ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN are set; the API key "
            "takes precedence (pay-as-you-go). Unset it to use your Claude plan."
        )
