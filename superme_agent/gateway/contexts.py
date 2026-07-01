"""Context resolution — surface-facing id -> Core Context.

The global root ("Me") plus connected projects ("domains"). Domains are defined in the
spine's repo registry (config/repos.yaml); each resolves to its cwd and a knowledge home
in the central knowledge repo at `superme-knowledge/<id>-knowledge/` (renovation §4.11.2 —
centralized for all repos, not under each project's cwd). Each home splits into `core/`
(dashboard-consumed) + `dev/` (dev-knowledge). The DEFAULT workspace is the repo root and
IS the global context, so it's never listed as a separate domain.

This registry is the source of *which* domains exist. The future "knowledge bridge" — a
domains index inside global knowledge (status, meta, root, so SuperMe's own knowledge
knows its projects) — layers on top of this; it doesn't replace it.
"""

import logging

from ..core.context import Context
from ..core.spine import RepoConfig, get_spine

log = logging.getLogger("superme-agent")

GLOBAL_ID = "global"
# Every per-repo knowledge home splits into sub-roots by scope: core/ (the represented
# knowledge the Me/Domains dashboards consume — the domain/twin substance) and dev/ (internal
# dev-knowledge, wired only to the Development dashboard — not browsable). The `internal/`
# nesting was dropped in the renovation (§4.11.2); dev-knowledge is `<base>/dev` now.
CORE_SUBDIR = "core"

# The repo registry now lives in the system spine (config/repos.yaml), not registry.yaml —
# WI-3 cutover. The Context shape is unchanged: only its SOURCE moved. (Slack's separate
# runtime/workspaces.py still reads registry.yaml until the Slack rebuild folds it in.)


def _context_from_repo(rc: RepoConfig, mode: str) -> Context:
    """Build a Core Context from a spine RepoConfig. Home computation stays here (not in
    RepoConfig) so the exact Context shape every call-site depends on is preserved:
    knowledge_root = <base>/core (dashboard-consumed core knowledge); internal_root = <base>
    (callers append '/dev' → <base>/dev). The renovation dropped the old `internal/` nesting
    (§4.11.2), so internal_root is just the per-repo knowledge base now."""
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

    `global` (or unknown ids) -> the root SuperMe; a registered repo id -> that project's
    sub-SuperMe. `mode` ("core" | "dev") is supplied BY THE SURFACE — the Me chat runs
    "core"; the Development dashboard runs "dev". It is orthogonal to the layer (every
    (layer, mode) pair is valid) and selects the agent's charter + which harness plugins
    load — not a knowledge sandbox.
    """
    spine = get_spine()
    repos = spine.repos()
    cid = context_id or GLOBAL_ID
    rc = repos.get(cid)
    if rc is None:
        if cid != GLOBAL_ID:
            log.warning("unknown context_id %r; falling back to global", cid)
        rc = repos.get(GLOBAL_ID)
    if rc is None:  # repos.yaml empty/missing — synthesize a minimal global so we never crash
        from ..runtime.config import ROOT_DIR
        rc = RepoConfig(id=GLOBAL_ID, label="Me", cwd=ROOT_DIR, layer="global")
    return _context_from_repo(rc, mode)


def list_all() -> list[dict]:
    """Live contexts for the surfaces to render: global first, then each other repo."""
    repos = get_spine().repos()
    order = ([GLOBAL_ID] if GLOBAL_ID in repos else []) + [r for r in repos if r != GLOBAL_ID]
    return [{"id": repos[r].id, "label": repos[r].label, "layer": repos[r].layer,
             "cwd": str(repos[r].cwd)} for r in order]
