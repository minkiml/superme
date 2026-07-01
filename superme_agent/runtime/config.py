"""Configuration, paths, and shared setup for the SuperMe agent.

Surface-neutral: this module holds paths, .env loading, and logging only. It must NOT
require any surface's credentials (e.g. Slack tokens), so the Core can import it
without a Slack environment. Slack-specific config lives in the Slack adapter
(runtime/slack_app.py, runtime/permissions.py).

Path model (the host-app vs workspace split):
  APP_DIR   = the host application itself (this package). Holds the spine (.system.db,
              config/), runtime state, the harness, and config. Anchored to the CODE
              location, so it never
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
SELF_FILE = HARNESS_DIR / "SELF.md"
# Per-mode system-prompt charters (universal, single source per mode). SELF.md = WHO
# (all modes); the charter = WHAT MODE. Selected by Context.mode in agent_service.
CHARTER_FILES = {
    "core": HARNESS_DIR / "core-charter.md",
    "dev": HARNESS_DIR / "dev-charter.md",
}
# Per-mode harness plugins. Skill discovery is flat (<plugin>/skills/<name>/SKILL.md), so
# grouping is done by SEPARATE plugins, and mode selects which load (see plugins_for).
PLUGINS_DIR = HARNESS_DIR / "plugins"
SHARED_PLUGIN_DIR = PLUGINS_DIR / "superme-shared"   # every mode
CORE_PLUGIN_DIR = PLUGINS_DIR / "superme-core"       # mode=core
DEV_PLUGIN_DIR = PLUGINS_DIR / "superme-dev"         # mode=dev
# UNIVERSAL constitution home (WI-8): learned, always-on behavior-governing content, one file per
# item, assembled into the system prompt for the active scope×mode. `constitution/<scope>/`. Per-repo
# constitution lives at local-harness/<id>/<scope>/constitution/ (see spine.RepoConfig). The
# hand-authored SELF.md/charters are NEVER auto-written — learned items live here, separately.
CONSTITUTION_DIR = HARNESS_DIR / "constitution"


def plugins_for(mode: str, op_home: Path | None = None) -> list[str]:
    """Local-plugin paths for a turn: the universal harness (shared + the mode's plugin), then —
    additively — this repo's per-repo operational plugin when present (renovation §4.11.1). The
    universal plugins scope SuperMe's OWN skills by mode; `op_home` is the per-repo operational
    cell `local-harness/<id>/<mode>` (repo-specialized skills/agents), loaded only if it carries a
    plugin manifest. (Was the knowledge-tree `practice/<mode>`, now under the code — see
    spine.RepoConfig.operational_home.)"""
    mode_dir = DEV_PLUGIN_DIR if mode == "dev" else CORE_PLUGIN_DIR
    paths = [str(SHARED_PLUGIN_DIR), str(mode_dir)]
    if op_home is not None:
        # A loadable plugin needs a manifest; a bare placeholder dir is skipped (nothing to load yet).
        if (Path(op_home) / ".claude-plugin" / "plugin.json").is_file():
            paths.append(str(op_home))
    return paths
REGISTRY_FILE = APP_DIR / "config" / "registry.yaml"
# --- System spine (PRD §4.11.3 — the System/Repo/Session/Run keystone) -----------
# Static-meta lives in git-tracked, hand-editable YAML; live-status lives in a SQLite
# DB. The spine LOADS the YAML + OWNS the DB, presenting one authoritative API. (These
# are additive in WI-1 — nothing reads them until the WI-3 cutover; the old stores
# `registry.yaml`/`.sessions.json`/`.context_models.json` remain the live source meanwhile.)
CONFIG_DIR = APP_DIR / "config"
SYSTEM_CONFIG_FILE = CONFIG_DIR / "system.yaml"      # singleton static-meta
REPOS_CONFIG_FILE = CONFIG_DIR / "repos.yaml"        # repo registry (scope-aware; supersedes registry.yaml)
SYSTEM_DB_FILE = APP_DIR / ".system.db"              # live: session, run, model_override
# Knowledge home (renovation §4.11.2): ONE gitignored knowledge repo at the repo root,
# holding per-repo sub-homes `superme-knowledge/<id>-knowledge/{core,dev}`. Centralized for
# ALL repos (single-owner → central review), keyed by repo id — see spine.RepoConfig. This
# replaces the old per-cwd `superme-global-knowledge/` + `<cwd>/superme-knowledge` split.
KNOWLEDGE_REPO_DIR = Path(
    os.environ.get("SUPERME_KNOWLEDGE_REPO", str(ROOT_DIR / "superme-knowledge"))
)
# Per-repo OPERATIONAL home (additive over the universal harness/): under the CODE tree,
# keyed by repo id × mode — `local-harness/<id>/<mode>` (renovation §4.11.1). This is the
# relocation of the old knowledge-tree `practice/<mode>` — operational lives with the code.
LOCAL_HARNESS_DIR = APP_DIR / "local-harness"
# Channel→workspace state {channel: {workspace, locked}}, managed live from Slack
# (not committed; per-environment).
CHANNELS_FILE = APP_DIR / ".channel_workspaces.json"
# Channel→model override {channel: "haiku"|"sonnet"|"opus"}, set live from Slack
# (`@bot /model …`). Unset channels use the CLI default model.
MODELS_FILE = APP_DIR / ".channel_models.json"

# --- Shared command layer + "/" palette (1.5) -----------------------------------
# (Per-context model overrides moved to the spine's `model_override` table in WI-3.)
# Cached slash-command lists per context, so the web "/" palette is available on
# connect (the SDK only reveals the list during a turn, so we remember the last one).
SLASH_COMMANDS_FILE = APP_DIR / ".slash_commands.json"

# --- Dev-dashboard operational store (1.5k-ops) ---------------------------------
# Host-app SQLite for the dev cockpit's OPERATIONAL state — the inbox triage queue
# today; agent runs / sessions / evals / approvals later. Durable *knowledge* stays in
# markdown files (per context); this DB holds queue/run/status state, keyed by
# context_id, and is never committed (D-013 / D-014).
DEV_DB_FILE = APP_DIR / ".dev.db"

# --- Sessions (Stage B / 1.5) ---------------------------------------------------
# The cross-surface session log now lives in the system spine's `session` table
# (.system.db), keyed by repo+cwd — see core/spine.py (replaced `.sessions.json` in WI-3).
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
