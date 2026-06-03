"""Channel → workspace resolution (Layer 3).

Two halves, deliberately separated:
  - DEFINITIONS (registry.yaml) — workspace name → cwd + extras. Code-side, committed.
    Adding a new workspace = edit registry.yaml (+ restart).
  - LINKS (.channel_links.json) — channel id → workspace name. Managed LIVE from
    Slack (`@bot workspace use <name>`), persisted, no code edit / no restart.

resolve(channel) = links.get(channel) → definition, else the default workspace.
"""

import json
import logging
from pathlib import Path
from dataclasses import dataclass, field

import yaml

from .config import ROOT_DIR, REGISTRY_FILE, LINKS_FILE, THREAD_WS_FILE

log = logging.getLogger("superme-agent")


@dataclass
class Workspace:
    name: str
    cwd: Path
    persona_append: str = ""
    extra_mcp: list = field(default_factory=list)  # names; wired when servers exist
    label: str = ""                                 # display name (defaults to name)


# --- definitions: loaded once from registry.yaml at startup ------------------
def _load_registry() -> dict:
    try:
        return yaml.safe_load(REGISTRY_FILE.read_text()) or {}
    except FileNotFoundError:
        log.warning("registry.yaml not found at %s; default workspace only.", REGISTRY_FILE)
        return {}
    except yaml.YAMLError as e:
        log.warning("registry.yaml invalid (%s); default workspace only.", e)
        return {}


_REGISTRY = _load_registry()
_DEFS: dict = _REGISTRY.get("workspaces", {}) or {}
_DEFAULT: str = _REGISTRY.get("default_workspace", "default")


# --- links: persisted, mutated live by Slack commands ------------------------
def _load_links() -> dict:
    try:
        return json.loads(LINKS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_LINKS: dict = _load_links()


def _save_links() -> None:
    try:
        LINKS_FILE.write_text(json.dumps(_LINKS, indent=2))
    except OSError as e:
        log.warning("Could not persist channel links: %s", e)


def _build(name: str) -> Workspace:
    """Build a Workspace from a definition (falls back to default if unknown)."""
    if name not in _DEFS:
        name = _DEFAULT
    spec = _DEFS.get(name) or {}
    cwd = Path(spec.get("cwd", "."))
    if not cwd.is_absolute():
        cwd = (ROOT_DIR / cwd).resolve()
    return Workspace(
        name=name,
        cwd=cwd,
        persona_append=(spec.get("persona_append") or "").strip(),
        extra_mcp=spec.get("extra_mcp") or [],
        label=(spec.get("label") or name),
    )


def default_workspace() -> str:
    return _DEFAULT


def known_workspaces() -> list[str]:
    """Names of all defined workspaces (from registry.yaml)."""
    return sorted(_DEFS.keys())


def current(channel_id: str) -> str:
    """The workspace name a channel is currently linked to."""
    return _LINKS.get(channel_id, _DEFAULT)


def resolve(channel_id: str) -> Workspace:
    """Map a Slack channel to its workspace (default if unlinked)."""
    return _build(current(channel_id))


def link(channel_id: str, name: str) -> tuple[bool, str]:
    """Link a channel to a defined workspace. Returns (ok, message-for-Slack)."""
    if name not in _DEFS:
        avail = ", ".join(f"`{n}`" for n in known_workspaces())
        return False, f"Unknown workspace `{name}`. Defined: {avail}."
    ws = _build(name)
    if not ws.cwd.is_dir():
        return False, (
            f"Workspace `{name}` points at a path that doesn't exist:\n"
            f"> `{ws.cwd}`\nFix its `cwd` in registry.yaml, then try again."
        )
    _LINKS[channel_id] = name
    _save_links()
    return True, f"✅ This channel is now the *{name}* workspace.\n> cwd: `{ws.cwd}`"


def unlink(channel_id: str) -> str:
    """Reset a channel back to the default workspace."""
    _LINKS.pop(channel_id, None)
    _save_links()
    return f"↩️ This channel is back to the default workspace (*{_DEFAULT}*)."


# --- per-thread pin: a thread keeps the workspace it was BORN in -------------
# The channel link decides the workspace for NEW threads only; existing threads
# stay pinned, so switching a channel's workspace never contaminates live threads.
def _load_pins() -> dict:
    try:
        return json.loads(THREAD_WS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


_PINS: dict = _load_pins()


def _save_pins() -> None:
    try:
        THREAD_WS_FILE.write_text(json.dumps(_PINS, indent=2))
    except OSError as e:
        log.warning("Could not persist thread workspace pins: %s", e)


def pinned(thread_ts: str) -> str | None:
    """The workspace a thread is pinned to, or None if it hasn't started yet."""
    return _PINS.get(thread_ts)


def workspace_for_thread(thread_ts: str, channel_id: str) -> Workspace:
    """The workspace for a thread: its pin if it has one, else the channel's
    current link (pinned now so the rest of the thread stays consistent)."""
    name = _PINS.get(thread_ts)
    if name is None or name not in _DEFS:
        name = current(channel_id)          # new thread inherits the channel link
        _PINS[thread_ts] = name
        _save_pins()
    return _build(name)
