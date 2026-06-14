"""Context resolution — surface-facing id -> Core Context.

Stage B1 supports only the global (root) context: the "Me" SuperMe, running in the
repo root. Local/project contexts (sub-SuperMes) join here in Phase 2, each mapping a
project id to its cwd + knowledge_root. The knowledge_root is wired in Stage C once
superme-global-knowledge/ exists.
"""

import logging

from ..runtime.config import ROOT_DIR
from ..core.context import Context

log = logging.getLogger("superme-agent")

GLOBAL_ID = "global"


def resolve(context_id: str | None) -> Context:
    """Resolve a surface-facing context id to a Core Context.

    Unknown ids fall back to the global root (B1 has no other contexts yet).
    """
    cid = context_id or GLOBAL_ID
    if cid != GLOBAL_ID:
        log.warning("unknown context_id %r; falling back to global", cid)
    return Context(
        layer="global",
        id=GLOBAL_ID,
        cwd=ROOT_DIR,
        knowledge_root=None,      # wired in Stage C (superme-global-knowledge/)
        label="Me (global)",
    )
