"""Configuration, paths, and shared setup for the SuperMe agent.

Surface-neutral: this module holds paths, .env loading, and logging only. It must NOT
require any surface's credentials (e.g. Slack tokens), so the Core can import it
without a Slack environment. Slack-specific config lives in the Slack adapter
(runtime/slack_app.py, runtime/permissions.py).

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
# Channel→workspace state {channel: {workspace, locked}}, managed live from Slack
# (not committed; per-environment).
CHANNELS_FILE = APP_DIR / ".channel_workspaces.json"
# Channel→model override {channel: "haiku"|"sonnet"|"opus"}, set live from Slack
# (`@bot /model …`). Unset channels use the CLI default model.
MODELS_FILE = APP_DIR / ".channel_models.json"
ENV_FILE = APP_DIR / ".env"

load_dotenv(ENV_FILE)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("superme-agent")

# SuperMe Core daemon (Stage B): localhost only, single-owner, no auth.
DAEMON_HOST = os.environ.get("SUPERME_DAEMON_HOST", "127.0.0.1")
DAEMON_PORT = int(os.environ.get("SUPERME_DAEMON_PORT", "8787"))
# How long the daemon waits for a surface to answer an approval before denying (s).
DAEMON_APPROVAL_TIMEOUT = 180


def warn_on_conflicting_auth() -> None:
    """Subscription auth (Claude Max) wins only if no API key shadows it."""
    if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        log.warning(
            "Both ANTHROPIC_API_KEY and CLAUDE_CODE_OAUTH_TOKEN are set; the API key "
            "takes precedence (pay-as-you-go). Unset it to use your Claude plan."
        )
