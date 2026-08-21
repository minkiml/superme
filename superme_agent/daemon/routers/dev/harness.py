"""Manage-Harness routes: SuperMe's own universal skills and agents, the chat palette, and the
inventory of what the learning loop published.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ....gateway import contexts
from ...app_state import DevStore, get_dev_store
from ...deps import load_slash_cache, local_harness_root, published_ident
from ...schemas.dev.harness import (AssetActionBody, AssetActionResponse, AssetsResponse,
                                    ConstitutionFileBody, ConstitutionFileResponse,
                                    ConstitutionFileSaveResponse, ConstitutionToggleBody,
                                    ConstitutionToggleResponse, ConstitutionsResponse,
                                    DeputyMandateBody, DeputyMandateResponse,
                                    DeputyMandateSaveResponse, FoundationFileBody,
                                    FoundationFileSaveResponse, FoundationResponse,
                                    HarnessPluginsResponse, LocalPluginsResponse, PaletteResponse,
                                    PluginFileBody, PluginFileResponse, PluginFileSaveResponse,
                                    PublishedDeleteResponse, PublishedFileBody,
                                    PublishedFileResponse, PublishedFileSaveResponse,
                                    PublishedResponse, PublishedToggleBody,
                                    PublishedToggleResponse)

log = logging.getLogger("superme-agent")

router = APIRouter()


# --- universal harness plugin tree (Manage-Harness "Skills & Agents" tab) --------------------
@router.get("/dev/harness/plugins", response_model=HarnessPluginsResponse,
            response_model_exclude_unset=True)
async def dev_harness_plugins() -> dict:
    """SuperMe's OWN universal skills and agents, grouped by scope, read straight from the plugin
    frontmatter. Per-repo operational artifacts are out of scope here."""
    from ....core.operational import list_harness_plugins
    from ....paths import DEV_PLUGIN_DIR, CORE_PLUGIN_DIR, SHARED_PLUGIN_DIR
    return {"scopes": list_harness_plugins(
        dev_dir=DEV_PLUGIN_DIR, core_dir=CORE_PLUGIN_DIR, shared_dir=SHARED_PLUGIN_DIR)}


@router.get("/dev/harness/local-plugins", response_model=LocalPluginsResponse,
            response_model_exclude_unset=True)
async def dev_harness_local_plugins(context_id: str = "global") -> dict:
    """This host's OWN local-harness skills + agents (its `local-harness/<id>/dev` plugin tree) — the
    per-repo Artifacts tab. Flat: the dev workspace is already dev-scoped, so no scope split."""
    from ....core.operational import read_plugin
    from ....paths import LOCAL_HARNESS_DIR
    plug = read_plugin(local_harness_root(context_id))
    return {"context_id": context_id, "skills": plug["skills"], "agents": plug["agents"]}


def _resolve_plugin_file(scope: str, kind: str, name: str, context_id: str = "global"):
    from ....core.operational import resolve_plugin_file
    from ....paths import DEV_PLUGIN_DIR, CORE_PLUGIN_DIR, SHARED_PLUGIN_DIR, LOCAL_HARNESS_DIR
    p = resolve_plugin_file(scope, kind, name, dev_dir=DEV_PLUGIN_DIR,
                            core_dir=CORE_PLUGIN_DIR, shared_dir=SHARED_PLUGIN_DIR,
                            local_dir=local_harness_root(context_id))
    if p is None or not p.is_file():
        raise HTTPException(status_code=404, detail="skill/agent not found")
    return p


@router.get("/dev/harness/plugin-file", response_model=PluginFileResponse)
async def dev_harness_plugin_file(scope: str, kind: str, name: str, context_id: str = "global") -> dict:
    """The raw markdown of one SuperMe skill/agent — the popup's preview + edit source. `scope='local'`
    reads this host's own `local-harness/<context_id>/dev` tree."""
    p = _resolve_plugin_file(scope, kind, name, context_id)
    return {"scope": scope, "kind": kind, "name": name, "path": str(p), "content": p.read_text()}


@router.put("/dev/harness/plugin-file", response_model=PluginFileSaveResponse)
async def dev_harness_plugin_file_save(body: PluginFileBody) -> dict:
    """Save edits to one SuperMe skill/agent file (the popup's edit mode). Takes effect on the next
    dev turn (plugins are read per-turn); no daemon restart needed for content edits."""
    p = _resolve_plugin_file(body.scope, body.kind, body.name, body.context_id)
    if not (body.content or "").strip():
        raise HTTPException(status_code=400, detail="content is empty")
    p.write_text(body.content)
    log.info("saved harness %s '%s' (%s)", body.kind, body.name, body.scope)
    return {"ok": True, "scope": body.scope, "kind": body.kind, "name": body.name}


# --- Foundations: SuperMe's universal identity + charters + learned constitution -------------
def _foundation_specs():
    from ....paths import SELF_FILE, CHARTER_FILES
    return [
        ("self", "SELF", "universal", SELF_FILE),
        ("dev-charter", "Dev charter", "dev", CHARTER_FILES["dev"]),
        ("core-charter", "Core charter", "core", CHARTER_FILES["core"]),
    ]


@router.get("/dev/harness/foundation", response_model=FoundationResponse)
async def dev_harness_foundation() -> dict:
    """SuperMe's repo-agnostic identity + machinery — the Foundations surface. SELF.md is WHO
    (all modes); the per-mode charters are WHAT-MODE (hand-authored, editable). Plus the LEARNED
    universal constitution (always-on rules the learning loop published), per mode."""
    from ....core.operational import read_constitution_dir
    from ....paths import CONSTITUTION_DIR
    files = []
    for key, label, scope, path in _foundation_specs():
        present = path.is_file()
        files.append({
            "key": key, "label": label, "scope": scope, "path": str(path),
            "present": present, "body": path.read_text() if present else "",
        })
    constitutions = []
    for mode in ("dev", "core"):
        for it in read_constitution_dir(CONSTITUTION_DIR / mode, origin="universal"):
            constitutions.append({
                "mode": mode, "slug": it["slug"], "enabled": it["enabled"],
                "foundational": it.get("foundational", False),
                "title": it["title"], "body": it["body"],
                "source": it.get("source"), "created": it.get("created"),
            })
    return {"files": files, "constitutions": constitutions}


@router.put("/dev/harness/foundation", response_model=FoundationFileSaveResponse)
async def dev_harness_foundation_save(body: FoundationFileBody) -> dict:
    """Save edits to an identity/charter file (SELF.md / dev-charter / core-charter). These are the
    hand-authored system-prompt sources; takes effect on the next turn (assembled per turn)."""
    path = next((p for key, _, _, p in _foundation_specs() if key == body.key), None)
    if path is None:
        raise HTTPException(status_code=404, detail=f"unknown foundation file '{body.key}'")
    if not (body.content or "").strip():
        raise HTTPException(status_code=400, detail="content is empty")
    path.write_text(body.content)
    log.info("saved foundation file '%s'", body.key)
    return {"ok": True, "key": body.key}


# --- Deputy mandate --- A governance artifact, so it lives in the harness cell: seeded on
# connect, wiped on disconnect.
@router.get("/dev/harness/deputy", response_model=DeputyMandateResponse)
async def dev_harness_deputy(context_id: str = "global") -> dict:
    """This repo's deputy mandate — the standing bar the deputy judges gates against while the owner
    is away. Seeded from a template on first read, so there is always a mandate to show/edit."""
    from ....core import deputy as deputy_core
    root = deputy_core.deputy_root(contexts.resolve(context_id, "dev"))
    content = deputy_core.read_mandate(root)
    return {"context_id": context_id, "path": str(deputy_core.mandate_path(root)), "content": content}


@router.put("/dev/harness/deputy", response_model=DeputyMandateSaveResponse)
async def dev_harness_deputy_save(body: DeputyMandateBody,
                                  dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Save edits to this repo's deputy mandate. Takes effect on the next deputy dispatch (the mandate
    is read per gate); no daemon restart needed."""
    from ....core import deputy as deputy_core
    if not (body.content or "").strip():
        raise HTTPException(status_code=400, detail="content is empty")
    p = deputy_core.mandate_path(deputy_core.deputy_root(contexts.resolve(body.context_id, "dev")))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.content)
    log.info("saved deputy mandate for '%s'", body.context_id)
    dev_store.log_event(
        body.context_id, "harness.edited", "Edited the deputy mandate",
        scope="dev", actor="owner", meta={"artifact": "deputy-mandate"})
    return {"ok": True, "context_id": body.context_id}


# --- the "/" palette --- Internal pipeline skills are hidden; native commands come from the SDK's
# cached list.
_HIDE_CATEGORIES = {"learning"}


@router.get("/dev/palette", response_model=PaletteResponse)
async def dev_palette(context_id: str = "global", mode: str = "dev") -> dict:
    """The chat "/" palette for a (context, mode): user-facing SuperMe skills for the active mode's
    plugin set (category not in the hidden set), computed live from disk, merged with the context's
    cached external/native commands."""
    from ....core.operational import list_palette_skills
    from ....paths import plugins_for, LOCAL_HARNESS_DIR
    op_home = local_harness_root(context_id, mode) if context_id else None
    skills = list_palette_skills([Path(p) for p in plugins_for(mode, op_home)])
    visible = [s["command"] for s in skills
               if (s.get("category") or "").strip().lower() not in _HIDE_CATEGORIES]
    # Native commands = cached entries NOT in any of OUR namespaces (we serve those fresh, per mode).
    known_ns = {"superme-shared", "superme-core", "superme-dev",
                f"{context_id}-dev", f"{context_id}-core"}
    cached = load_slash_cache().get(context_id) or []
    external = [c for c in cached if (":" not in c) or (c.split(":", 1)[0] not in known_ns)]
    return {"context_id": context_id, "mode": mode,
            "commands": sorted(set(visible) | set(external))}


# --- Published inventory --- Driven by published proposals, so shipped harness skills never
# appear here.
@router.get("/dev/harness/published", response_model=PublishedResponse)
async def dev_harness_published(context_id: str = "global",
                                dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """The Published inventory — every LEARNED artifact the owner published, reconciled with live
    on-disk state (present + enabled). One row per published proposal, newest first."""
    from ....core import operational as ops
    rows = []
    for prop in dev_store.list_memory_proposals(context_id, status="published"):
        form, scope, repo_id, slug = published_ident(prop, context_id)
        try:
            st = ops.published_state(form, scope, repo_id, slug)
        except Exception:
            st = {"present": False, "enabled": False}
        rows.append({
            "proposal_id": prop["id"], "form": form, "scope": scope, "slug": slug,
            "title": prop["title"], "summary": prop.get("summary"),
            "created": prop.get("created_at"), **st,
        })
    return {"context_id": context_id, "published": rows}


@router.patch("/dev/harness/published/{proposal_id}", response_model=PublishedToggleResponse,
               response_model_exclude_unset=True)
async def dev_harness_published_toggle(proposal_id: int, body: PublishedToggleBody,
                                       dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Enable/disable a published artifact at runtime (no delete). Constitution flips its frontmatter
    flag; a skill/agent moves between the live plugin tree and its `.disabled/` shadow. Effective on
    the next dev turn."""
    from ....core import operational as ops
    prop = dev_store.get_memory_proposal(proposal_id)
    if not prop or prop["context_id"] != body.context_id or prop["status"] != "published":
        raise HTTPException(status_code=404, detail="published artifact not found")
    form, scope, repo_id, slug = published_ident(prop, body.context_id)
    try:
        res = ops.set_published_enabled(form, scope, repo_id, slug, body.enabled)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"toggle failed: {e}")
    if res is None:
        raise HTTPException(status_code=404, detail="artifact file missing on disk")
    dev_store.log_event(
        body.context_id, "harness.toggled",
        f"{'Enabled' if body.enabled else 'Disabled'} {form} '{prop['title']}'",
        scope="dev", actor="owner",
        meta={"proposal_id": proposal_id, "form": form, "enabled": body.enabled})
    return {"ok": True, "proposal_id": proposal_id, **res}


# --- Constitutions --- A DISK SCAN, so it covers hand-authored, system and learned constitutions
# alike.
@router.get("/dev/harness/constitutions", response_model=ConstitutionsResponse)
async def dev_harness_constitutions(context_id: str = "global") -> dict:
    """Every constitution in this context's scope: universal-dev + this host's local (`repo_dev`),
    each with live `enabled` state. Core is deferred (no core constitutions yet)."""
    from ....core.operational import read_constitution_dir
    from ....paths import CONSTITUTION_DIR, LOCAL_HARNESS_DIR
    out = []

    def collect(items: list, scope: str, mode: str) -> None:
        for it in items:
            out.append({
                "slug": it["slug"], "scope": scope, "mode": mode, "origin": it["origin"],
                "enabled": it["enabled"], "foundational": it.get("foundational", False),
                "title": it["title"],
                "description": it.get("description"), "body": it["body"],
                "source": it.get("source"), "created": it.get("created"),
                "updated": it.get("updated"),
            })

    collect(read_constitution_dir(CONSTITUTION_DIR / "dev", origin="universal"), "universal_dev", "dev")
    collect(read_constitution_dir(local_harness_root(context_id) / "constitution",
                                  origin="repo"), "repo_dev", "dev")
    return {"context_id": context_id, "constitutions": out}


@router.patch("/dev/harness/constitutions/{slug}", response_model=ConstitutionToggleResponse,
              response_model_exclude_unset=True)
async def dev_harness_constitution_toggle(slug: str, body: ConstitutionToggleBody,
                                          dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Enable/disable ANY constitution by (scope, slug) — no proposal needed. A disabled constitution
    leaves the always-on catalog AND becomes unpullable (both filter on `enabled`), so it is fully
    inert. Effective on the next dev turn."""
    from ....core import operational as ops
    if body.scope not in ("universal_dev", "repo_dev"):
        raise HTTPException(status_code=400, detail=f"scope '{body.scope}' is not manageable")
    # A foundational constitution is pinned always-on: a charter consults it by name, so disabling
    # would dangle that pull.
    if not body.enabled:
        p = _resolve_constitution_file(body.scope, slug, body.context_id)
        from ....core.operational import parse_frontmatter, is_foundational
        if is_foundational(parse_frontmatter(p.read_text())[0]):
            raise HTTPException(
                status_code=409,
                detail=f"'{slug}' is foundational (a charter consults it by name) — it can't be disabled.")
    repo_id = body.context_id if body.scope == "repo_dev" else None
    try:
        res = ops.set_published_enabled("constitution", body.scope, repo_id, slug, body.enabled)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"toggle failed: {e}")
    if res is None:
        raise HTTPException(status_code=404, detail="constitution not found on disk")
    dev_store.log_event(
        body.context_id, "harness.toggled",
        f"{'Enabled' if body.enabled else 'Disabled'} constitution '{slug}'",
        scope="dev", actor="owner",
        meta={"scope": body.scope, "slug": slug, "enabled": body.enabled})
    return {"ok": True, "slug": slug, "scope": body.scope, **res}


# --- Constitution edit: raw-file GET/PUT (frontmatter intact) ---------------------------------
def _resolve_constitution_file(scope: str, slug: str, context_id: str) -> Path:
    """The on-disk `.md` for one constitution, by (scope, slug). Universal lives under
    `constitution/<mode>/`; a host's local under `local-harness/<ctx>/<mode>/constitution/`. Matches
    on the item's slug (frontmatter `name` or filename stem), not a bare `<slug>.md` guess."""
    from ....core.operational import read_constitution_dir
    from ....paths import CONSTITUTION_DIR, LOCAL_HARNESS_DIR
    if scope.startswith("universal_"):
        mode = scope.split("_", 1)[1]
        home, origin = CONSTITUTION_DIR / mode, "universal"
    elif scope.startswith("repo_"):
        mode = scope.split("_", 1)[1]
        home, origin = local_harness_root(context_id, mode) / "constitution", "repo"
    else:
        raise HTTPException(status_code=400, detail=f"scope '{scope}' is not editable")
    for it in read_constitution_dir(home, origin=origin):
        if it["slug"] == slug:
            return Path(it["path"])
    raise HTTPException(status_code=404, detail=f"constitution '{slug}' not found in scope '{scope}'")


@router.get("/dev/harness/constitution-file", response_model=ConstitutionFileResponse)
async def dev_harness_constitution_file(slug: str, scope: str, context_id: str = "global") -> dict:
    """The raw markdown (frontmatter intact) of one constitution — the popup's edit source. The
    catalog GET returns only the stripped body + description, so editing pulls the full file here."""
    p = _resolve_constitution_file(scope, slug, context_id)
    return {"slug": slug, "scope": scope, "path": str(p), "content": p.read_text()}


@router.put("/dev/harness/constitution-file", response_model=ConstitutionFileSaveResponse)
async def dev_harness_constitution_file_save(body: ConstitutionFileBody,
                                             dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Save edits to one constitution file (the popup's edit mode) — the full raw markdown, frontmatter
    kept so `enabled`/`description` survive. Takes effect on the next dev turn (constitutions are read
    per-turn); no daemon restart needed."""
    p = _resolve_constitution_file(body.scope, body.slug, body.context_id)
    if not (body.content or "").strip():
        raise HTTPException(status_code=400, detail="content is empty")
    p.write_text(body.content)
    log.info("saved constitution '%s' (%s)", body.slug, body.scope)
    dev_store.log_event(
        body.context_id, "harness.edited", f"Edited constitution '{body.slug}'",
        scope="dev", actor="owner", meta={"scope": body.scope, "slug": body.slug})
    return {"ok": True, "slug": body.slug, "scope": body.scope}


# --- asset pool (per-repo opt-in constitutional knowledge) ------------------------------------

@router.get("/dev/harness/assets", response_model=AssetsResponse)
async def dev_harness_assets(context_id: str = "global") -> dict:
    """The shared asset pool (opt-in constitutional knowledge, e.g. `sql-expert`), each flagged with
    whether THIS repo has ADOPTED and ENABLED it. The pool is shared; adoption is per-repo (no body
    copy). Onboarding auto-adopts the confidently-relevant ones; the owner curates from here."""
    from ....core.operational import read_asset_pool, repo_asset_states
    from ....paths import LOCAL_HARNESS_DIR
    states = repo_asset_states(local_harness_root(context_id) / "constitution")
    out = [{
        "slug": it["slug"], "title": it["title"],
        "description": it.get("description"), "body": it["body"],
        "adopted": it["slug"] in states, "enabled": states.get(it["slug"], False),
    } for it in read_asset_pool()]
    return {"context_id": context_id, "assets": out}


@router.patch("/dev/harness/assets/{slug}", response_model=AssetActionResponse)
async def dev_harness_asset_action(slug: str, body: AssetActionBody,
                                   dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Per-repo asset curation: `adopt` (+ Add), `enable`/`disable` (toggle, keeps adoption), or `drop`
    (un-adopt). Writes the repo's `.assets` list — no body copy. Takes effect on the next dev turn."""
    from ....core.operational import read_asset_pool, set_repo_asset, drop_repo_asset
    from ....paths import LOCAL_HARNESS_DIR
    if slug not in {it["slug"] for it in read_asset_pool()}:
        raise HTTPException(status_code=404, detail="asset not found in the pool")
    home = local_harness_root(body.context_id) / "constitution"
    if body.action == "drop":
        states = drop_repo_asset(home, slug)
    elif body.action in ("adopt", "enable"):
        states = set_repo_asset(home, slug, True)
    elif body.action == "disable":
        states = set_repo_asset(home, slug, False)
    else:
        raise HTTPException(status_code=400, detail=f"unknown action '{body.action}'")
    dev_store.log_event(
        body.context_id, "harness.asset", f"Asset '{slug}' — {body.action}",
        scope="dev", actor="owner", meta={"slug": slug, "action": body.action})
    return {"ok": True, "slug": slug, "action": body.action,
            "adopted": slug in states, "enabled": states.get(slug, False)}


@router.delete("/dev/harness/published/{proposal_id}", response_model=PublishedDeleteResponse,
                response_model_exclude_unset=True)
async def dev_harness_published_delete(proposal_id: int, context_id: str = "global",
                                       dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Remove a published artifact from disk entirely and retire its proposal (history kept). The
    loader stops seeing it next turn."""
    from ....core import operational as ops
    prop = dev_store.get_memory_proposal(proposal_id)
    if not prop or prop["context_id"] != context_id or prop["status"] != "published":
        raise HTTPException(status_code=404, detail="published artifact not found")
    form, scope, repo_id, slug = published_ident(prop, context_id)
    try:
        removed = ops.delete_published(form, scope, repo_id, slug)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"delete failed: {e}")
    dev_store.set_proposal_status(proposal_id, "retired")
    dev_store.log_event(
        context_id, "harness.deleted",
        f"Deleted {form} '{prop['title']}' (retired)",
        scope="dev", actor="owner",
        meta={"proposal_id": proposal_id, "form": form, "removed": removed})
    return {"ok": True, "proposal_id": proposal_id, "removed": removed}


def _published_prop(proposal_id: int, context_id: str, dev_store: DevStore):
    """Resolve a published proposal + its (form, scope, repo_id, slug) + editable file path."""
    from ....core import operational as ops
    prop = dev_store.get_memory_proposal(proposal_id)
    if not prop or prop["context_id"] != context_id or prop["status"] != "published":
        raise HTTPException(status_code=404, detail="published artifact not found")
    form, scope, repo_id, slug = published_ident(prop, context_id)
    path = ops.published_file(form, scope, repo_id, slug)
    if path is None:
        raise HTTPException(status_code=404, detail="artifact file missing on disk")
    return prop, form, scope, slug, path


@router.get("/dev/harness/published/{proposal_id}/file", response_model=PublishedFileResponse)
async def dev_harness_published_file(proposal_id: int, context_id: str = "global",
                                     dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """The raw markdown source of a published artifact — for the Published-tab preview/edit."""
    _, form, scope, slug, path = _published_prop(proposal_id, context_id, dev_store)
    return {"proposal_id": proposal_id, "form": form, "scope": scope, "slug": slug,
            "path": str(path), "content": path.read_text()}


@router.put("/dev/harness/published/{proposal_id}/file", response_model=PublishedFileSaveResponse)
async def dev_harness_published_file_save(proposal_id: int, body: PublishedFileBody,
                                          dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Save edits to a published artifact's source. Takes effect on the next dev turn."""
    if not (body.content or "").strip():
        raise HTTPException(status_code=400, detail="content is empty")
    prop, form, _, _, path = _published_prop(proposal_id, body.context_id, dev_store)
    path.write_text(body.content)
    dev_store.log_event(
        body.context_id, "harness.edited", f"Edited {form} '{prop['title']}'",
        scope="dev", actor="owner", meta={"proposal_id": proposal_id, "form": form})
    return {"ok": True, "proposal_id": proposal_id}
