"""Configuration, paths, and shared setup for the SuperMe agent.

Path model (the host-app vs workspace split):
  APP_DIR   = the host application itself (this package). Holds .env, .sessions.json,
              the harness, and config. Anchored to the CODE location, so it never
              moves when the agent roams into a different workspace.
  ROOT_DIR  = the repo root = the DEFAULT workspace cwd (and future global harness).
"""

import os
import logging
from pathlib import Path

from dotenv import load_dotenv

# config.py lives at superme_agent/runtime/config.py
APP_DIR = Path(__file__).resolve().parent.parent       # superme_agent/
ROOT_DIR = APP_DIR.parent                               # repo root = default workspace

HARNESS_DIR = APP_DIR / "harness"
PERSONA_FILE = HARNESS_DIR / "persona.md"
PLUGIN_DIR = HARNESS_DIR / "plugin"
REGISTRY_FILE = APP_DIR / "config" / "registry.yaml"
SESSIONS_FILE = APP_DIR / ".sessions.json"
# Channel→workspace links, managed live from Slack (not committed; per-environment).
LINKS_FILE = APP_DIR / ".channel_links.json"
# Per-thread workspace pin (a thread keeps the workspace it was born in).
THREAD_WS_FILE = APP_DIR / ".thread_workspaces.json"
ENV_FILE = APP_DIR / ".env"

load_dotenv(ENV_FILE)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("superme-agent")

# Slack credentials (Socket Mode needs both). Missing either is a hard error.
SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

# How long to wait for a human to react ✅/❌ before auto-denying (seconds).
APPROVAL_TIMEOUT = 180

# Emoji names that count as approve / deny on an approval card.
APPROVE_REACTIONS = {"white_check_mark", "heavy_check_mark", "+1", "thumbsup"}
DENY_REACTIONS = {"x", "no_entry", "no_entry_sign", "-1", "thumbsdown"}
APPROVE_EMOJI_SEED = "white_check_mark"
DENY_EMOJI_SEED = "x"


def warn_on_conflicting_auth() -> None:
    """Subscription auth (Claude Max) wins only if no API key shadows it."""
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        log.warning(
            "Both ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN are set; the API key "
            "takes precedence (pay-as-you-go). Unset it to use your Claude plan."
        )
