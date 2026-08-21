"""Context resolution — surface-facing id to Core Context.

The global root plus the connected projects the spine's repo registry defines, each with a
cwd and a knowledge home. The repo root IS the global context, never a project.
"""

import logging

from ..core.context import Context
from ..core.spine import RepoConfig, get_spine

log = logging.getLogger("superme-agent")

GLOBAL_ID = "global"
# A knowledge home splits by scope: `core/` is what the dashboards render, `dev/` is not.
CORE_SUBDIR = "core"

def _context_from_repo(rc: RepoConfig, mode: str) -> Context:
    """Build a Core Context from a spine RepoConfig.

    Home computation stays here rather than on RepoConfig, so the exact Context shape every
    call-site depends on is preserved."""
    home = rc._knowledge_base()
    return Context(
        layer=rc.layer,
        id=rc.id,
        mode=mode,
        cwd=rc.cwd,
        knowledge_root=home / CORE_SUBDIR,
        internal_root=home,
        persona_append=rc.persona_append,
        extra_mcp=list(rc.extra_mcp),
        label=rc.label,
    )


def resolve(context_id: str | None, mode: str = "core") -> Context:
    """Resolve a surface-facing context id (+ mode) to a Core Context.

    An unknown id falls back to the root SuperMe. `mode` comes FROM THE SURFACE: it picks the
    charter and the plugins, never a knowledge sandbox."""
    spine = get_spine()
    repos = spine.repos()
    cid = context_id or GLOBAL_ID
    rc = repos.get(cid)
    if rc is None:
        if cid != GLOBAL_ID:
            log.warning("unknown context_id %r; falling back to global", cid)
        rc = repos.get(GLOBAL_ID)
    if rc is None:  # no registry yet: synthesize a global rather than crash
        from ..paths import ROOT_DIR
        rc = RepoConfig(id=GLOBAL_ID, label="SuperMe hub", cwd=ROOT_DIR, layer="global")
    return _context_from_repo(rc, mode)


def list_all() -> list[dict]:
    """Live contexts for the surfaces to render: global first, then each other repo."""
    repos = get_spine().repos()
    order = ([GLOBAL_ID] if GLOBAL_ID in repos else []) + [r for r in repos if r != GLOBAL_ID]
    return [{"id": repos[r].id, "label": repos[r].label, "layer": repos[r].layer,
             "cwd": str(repos[r].cwd)} for r in order]
