"""Context resolution — surface-facing id -> Core Context.

The global root ("Me") plus connected projects ("domains"). Domains are defined in
registry.yaml (name -> cwd + extras); each resolves to its cwd and a project-local
knowledge_root at `<cwd>/superme-knowledge/` (the parallel of superme-global-knowledge/
at the repo root). The DEFAULT workspace is the repo root and IS the global context, so
it's never listed as a separate domain.

This registry is the source of *which* domains exist. The future "knowledge bridge" — a
domains index inside global knowledge (status, meta, root, so SuperMe's own knowledge
knows its projects) — layers on top of this; it doesn't replace it.
"""

import logging
from pathlib import Path

import yaml

from ..runtime.config import ROOT_DIR, KNOWLEDGE_GLOBAL_DIR, REGISTRY_FILE
from ..core.context import Context

log = logging.getLogger("superme-agent")

GLOBAL_ID = "global"
# Project-local knowledge folder (parallels superme-global-knowledge/ at the root).
DOMAIN_KNOWLEDGE_DIRNAME = "superme-knowledge"


def _load_registry() -> tuple[dict, str]:
    try:
        reg = yaml.safe_load(REGISTRY_FILE.read_text()) or {}
    except (FileNotFoundError, yaml.YAMLError) as e:
        log.warning("registry.yaml unavailable (%s); global context only.", e)
        return {}, "base"
    return (reg.get("workspaces") or {}), (reg.get("default_workspace") or "base")


_DEFS, _DEFAULT = _load_registry()


def _domain_ids() -> list[str]:
    """Workspace names that are real domains (every def except the default/root)."""
    return [name for name in _DEFS if name != _DEFAULT]


def _global() -> Context:
    return Context(
        layer="global",
        id=GLOBAL_ID,
        cwd=ROOT_DIR,
        knowledge_root=KNOWLEDGE_GLOBAL_DIR,
        label="Me",
    )


def _domain(name: str) -> Context:
    spec = _DEFS.get(name) or {}
    cwd = Path(spec.get("cwd", "."))
    if not cwd.is_absolute():
        cwd = (ROOT_DIR / cwd).resolve()
    return Context(
        layer="local",
        id=name,
        cwd=cwd,
        knowledge_root=cwd / DOMAIN_KNOWLEDGE_DIRNAME,
        persona_append=(spec.get("persona_append") or "").strip(),
        extra_mcp=spec.get("extra_mcp") or [],
        label=(spec.get("label") or name),
    )


def resolve(context_id: str | None) -> Context:
    """Resolve a surface-facing context id to a Core Context.

    `global` (or unknown ids) -> the root SuperMe; a registered domain name -> that
    project's sub-SuperMe.
    """
    cid = context_id or GLOBAL_ID
    if cid == GLOBAL_ID:
        return _global()
    if cid in _DEFS and cid != _DEFAULT:
        return _domain(cid)
    log.warning("unknown context_id %r; falling back to global", cid)
    return _global()


def list_all() -> list[dict]:
    """Live contexts for the surfaces to render: global first, then each domain."""
    out = [{"id": GLOBAL_ID, "label": "Me", "layer": "global", "cwd": str(ROOT_DIR)}]
    for name in _domain_ids():
        ctx = _domain(name)
        out.append({"id": ctx.id, "label": ctx.label, "layer": "local", "cwd": str(ctx.cwd)})
    return out
