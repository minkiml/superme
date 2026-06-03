"""Channel → workspace resolution (Layer 3).

A *workspace* is a cwd (a codebase/project) plus optional per-workspace extras
(persona addendum, extra MCP servers). Channels are linked to workspaces in
config/registry.yaml; unlinked channels fall back to the default workspace.

This is the only place that knows about the registry; the agent just asks
`resolve(channel_id)` and gets a Workspace.
"""

import logging
from pathlib import Path
from dataclasses import dataclass, field

import yaml

from .config import ROOT_DIR, REGISTRY_FILE

log = logging.getLogger("superme-agent")


@dataclass
class Workspace:
    name: str
    cwd: Path
    persona_append: str = ""
    extra_mcp: list = field(default_factory=list)  # names; wired when servers exist


def _load_registry() -> dict:
    try:
        return yaml.safe_load(REGISTRY_FILE.read_text()) or {}
    except FileNotFoundError:
        log.warning("registry.yaml not found at %s; using default workspace only.", REGISTRY_FILE)
        return {}
    except yaml.YAMLError as e:
        log.warning("registry.yaml is invalid (%s); using default workspace only.", e)
        return {}


_REGISTRY = _load_registry()


def _resolve_cwd(raw: str) -> Path:
    """Registry cwd may be '.' or relative (rooted at repo) or absolute."""
    p = Path(raw)
    return p if p.is_absolute() else (ROOT_DIR / p).resolve()


def resolve(channel_id: str) -> Workspace:
    """Map a Slack channel to its workspace (default if unlinked)."""
    workspaces = _REGISTRY.get("workspaces", {})
    name = (
        _REGISTRY.get("channels", {}).get(channel_id)
        or _REGISTRY.get("default_workspace", "default")
    )
    spec = workspaces.get(name) or {}
    return Workspace(
        name=name,
        cwd=_resolve_cwd(spec.get("cwd", ".")),
        persona_append=(spec.get("persona_append") or "").strip(),
        extra_mcp=spec.get("extra_mcp") or [],
    )
