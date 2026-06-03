"""Channel → workspace resolution (Layer 3).

Two halves, deliberately separated:
  - DEFINITIONS (registry.yaml) — workspace name → cwd + extras. Code-side, committed.
    Adding a new workspace = edit registry.yaml (+ restart).
  - LINKS (.channel_links.json) — channel id → workspace name. Set live from Slack
    (`@bot workspace use <name>`), persisted.

Locking model: a channel is freely configurable until its FIRST conversation; the
first agent run LOCKS the channel to its current workspace (.channel_locks.json).
After that the workspace can't change — one channel = one workspace for its life.
This guarantees a channel's threads never see a mid-stream workspace change.
"""

import json
import logging
from pathlib import Path
from dataclasses import dataclass, field

import yaml

from .config import ROOT_DIR, REGISTRY_FILE, LINKS_FILE, LOCKS_FILE

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


# --- small JSON persistence helpers ------------------------------------------
def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_json(path: Path, data: dict) -> None:
    try:
        path.write_text(json.dumps(data, indent=2))
    except OSError as e:
        log.warning("Could not persist %s: %s", path.name, e)


_LINKS: dict = _load_json(LINKS_FILE)   # channel -> workspace (configurable until locked)
_LOCKS: dict = _load_json(LOCKS_FILE)   # channel -> workspace (frozen)


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


def known_workspaces() -> list[str]:
    """Names of all defined workspaces (from registry.yaml)."""
    return sorted(_DEFS.keys())


def default_workspace() -> str:
    return _DEFAULT


def is_locked(channel_id: str) -> bool:
    return channel_id in _LOCKS


def current(channel_id: str) -> str:
    """The channel's effective workspace name (locked value wins, else the link)."""
    return _LOCKS.get(channel_id) or _LINKS.get(channel_id, _DEFAULT)


# --- configuration (only while a channel is still unlocked) ------------------
def link(channel_id: str, name: str) -> tuple[bool, str]:
    """Set a channel's workspace (only allowed before it locks)."""
    if is_locked(channel_id):
        return False, (
            f"🔒 This channel is locked to *{current(channel_id)}* (workspaces lock on "
            "first use). Use a different channel for another workspace."
        )
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
    _save_json(LINKS_FILE, _LINKS)
    return True, (
        f"✅ This channel will use the *{name}* workspace.\n> cwd: `{ws.cwd}`\n"
        "_Locks on the first conversation here._"
    )


def unlink(channel_id: str) -> str:
    """Reset a channel to default (only allowed before it locks)."""
    if is_locked(channel_id):
        return f"🔒 This channel is locked to *{current(channel_id)}* and can't be reset."
    _LINKS.pop(channel_id, None)
    _save_json(LINKS_FILE, _LINKS)
    return f"↩️ This channel will use the default workspace (*{_DEFAULT}*) until it locks."


# --- resolution at run time (locks the channel on first use) -----------------
def resolve_for_run(channel_id: str) -> Workspace:
    """The workspace for an agent run. Locks the channel on first use so its
    workspace is fixed for the rest of the channel's life."""
    if not is_locked(channel_id):
        _LOCKS[channel_id] = current(channel_id)
        _save_json(LOCKS_FILE, _LOCKS)
        log.info("locked channel %s to workspace %s", channel_id, _LOCKS[channel_id])
    return _build(_LOCKS[channel_id])
