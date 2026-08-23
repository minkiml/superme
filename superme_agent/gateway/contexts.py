"""Context resolution — surface-facing id to Core Context.

The global root plus the connected projects the spine's repo registry defines, each with a
cwd and a knowledge home. The repo root IS the global context, never a project.
"""

from ..core.vocab.context import Context
from ..core.spine import RepoConfig, get_spine

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


class UnknownContext(LookupError):
    """A context id no repo answers to. The daemon turns this into a 404."""

    def __init__(self, context_id: str) -> None:
        super().__init__(f"unknown context_id {context_id!r}")
        self.context_id = context_id


def resolve(context_id: str | None, mode: str = "core") -> Context:
    """Resolve a surface-facing context id and mode to a Core Context.

    An unknown id RAISES: answering as another project would write this work into its home."""
    repos = get_spine().repos()
    cid = context_id or GLOBAL_ID
    rc = repos.get(cid)
    if rc is None:
        if cid != GLOBAL_ID or repos:
            raise UnknownContext(cid)
        # No registry at all — first boot. Synthesizing the hub is the one safe substitution.
        from ..paths import ROOT_DIR
        rc = RepoConfig(id=GLOBAL_ID, label="SuperMe hub", cwd=ROOT_DIR, layer="global")
    return _context_from_repo(rc, mode)


def exists(context_id: str | None) -> bool:
    """Whether a context id names a live repo — for callers acting on a row that may outlive it."""
    return (context_id or GLOBAL_ID) in get_spine().repos()


def list_all() -> list[dict]:
    """Live contexts for the surfaces to render: global first, then each other repo."""
    repos = get_spine().repos()
    order = ([GLOBAL_ID] if GLOBAL_ID in repos else []) + [r for r in repos if r != GLOBAL_ID]
    return [{"id": repos[r].id, "label": repos[r].label, "layer": repos[r].layer,
             "cwd": str(repos[r].cwd)} for r in order]
