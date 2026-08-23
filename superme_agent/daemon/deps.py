"""Shared route dependencies — context resolution and small cross-surface helpers.

Depends only on the gateway, config and core, never on `server.py`, so routers can import it
without a cycle.
"""

import json
import logging

from fastapi import HTTPException

from ..core.auth import auth_status
from ..gateway import contexts
from ..paths import LOCAL_HARNESS_DIR, SLASH_COMMANDS_FILE

log = logging.getLogger("superme-agent")


# --- the credential gate --------------------------------------------------------

def needs_credential() -> None:
    """Refuse a route that would start agent work when nothing can authenticate.

    A dependency rather than a check per handler: the surface greys these actions out, but a
    surface that forgot one would otherwise start a run doomed to fail on its first token.
    """
    status = auth_status()
    if not status["ready"]:
        raise HTTPException(status_code=503, detail=status["detail"])


# --- context resolution ---------------------------------------------------------

def knowledge_root(context_id: str):
    """Resolve a context to its knowledge root, or 400 if it has none."""
    ctx = contexts.resolve(context_id)
    if not ctx.knowledge_root:
        raise HTTPException(status_code=400, detail="context has no knowledge root")
    return ctx.knowledge_root


def local_harness_root(context_id: str | None, mode: str = "dev"):
    """A context's own operational cell: `local-harness/<id>/<mode>`.

    RESOLVES THE ID THROUGH THE REGISTRY rather than joining the caller's string onto a path — a
    containment test is only worth the root it is given."""
    return LOCAL_HARNESS_DIR / contexts.resolve(context_id).id / mode


def dev_root(context_id: str):
    """Resolve a context to its dev-knowledge root, `<internal_root>/dev/`.

    dev-knowledge is physically separate from the knowledge the owner-facing dashboards read."""
    ctx = contexts.resolve(context_id)
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    return ctx.internal_root / "dev"


# --- "/" palette slash-command cache (shared by the palette route + ws/background turns) ----------

def load_slash_cache() -> dict:
    try:
        return json.loads(SLASH_COMMANDS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def cache_slash(context_id: str, slash_commands: list) -> None:
    """Remember a context's slash-command list so the "/" palette is ready on connect."""
    if not slash_commands:
        return
    cache = load_slash_cache()
    if cache.get(context_id) != slash_commands:
        cache[context_id] = slash_commands
        try:
            SLASH_COMMANDS_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
        except OSError as e:
            log.warning("could not persist slash cache: %s", e)


# --- learned-artifact identity (shared by the harness/published + learning/publish routes) ------

def proposal_slug(prop: dict) -> str:
    """The artifact slug: skill/agent → the `fields.name` (canonical); else `apply_target` or title."""
    from ..core.operational import slugify
    fields = prop.get("fields") or {}
    if prop["output_form"] in ("skill", "agent") and isinstance(fields, dict) and fields.get("name"):
        return slugify(str(fields["name"]))
    return slugify(prop.get("apply_target") or prop["title"])


def published_ident(prop: dict, context_id: str) -> tuple[str, str, str | None, str]:
    """(form, scope, repo_id, slug) for a published proposal — repo_dev binds repo_id to the context."""
    scope = prop["target_scope"]
    repo_id = context_id if scope == "repo_dev" else None
    return prop["output_form"], scope, repo_id, proposal_slug(prop)
