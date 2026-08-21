"""Work-item lifecycle routes: the phase pipeline, its quick-actions, review payload, call-trail and
the phase-advance gate.

Orchestration lives in `services/`; these routes are the thin HTTP layer over it.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ...app_state import (
    DevKnowledgeService, DevStore, SessionStore, SystemSpine,
    get_dev, get_dev_store, get_sessions, get_spine,
)
from ...deps import dev_root as _dev_root
from ....core import artifacts, kind_profiles, status_router
from ....gateway import contexts
from ...services.runs import (
    DEFAULT_RUN_MODEL, _begin_run, _run_background_plan, build_item_timeline,
)
from ...services import scheduler, gates, clearance
from ...schemas.dev.work_items import (
    PlanResponse, WorkItemDetailResponse, WorkItemArtifactsResponse,
    WorkItemAdvanceResponse,
    WorkItemScaffoldResponse, WorkItemSeenResponse, WorkItemAutopilotResponse,
    WorkItemTimelineResponse, PromptExtractionStatusResponse, WorkItemDocEditResponse,
)

log = logging.getLogger("superme-agent")

router = APIRouter()


# --- background "Plan it": run /plan with no chat surface -------------------
class PlanBody(BaseModel):
    context_id: str = "global"
    model: str | None = None   # per-run model choice; None -> DEFAULT_RUN_MODEL
    effort: str | None = None  # per-run reasoning effort; None -> item/repo/system default


@router.post("/dev/work-items/{item_id}/run", response_model=PlanResponse)
async def dev_work_item_run(item_id: str, body: PlanBody,
                            dev: DevKnowledgeService = Depends(get_dev),
                            spine: SystemSpine = Depends(get_spine)) -> dict:
    """The owner's manual RUN — fire the current phase's own background run.

    The manual driver for a repo not on autopilot; on autopilot every phase fires itself. Refusals
    follow the same rule the drilldown's button reads."""
    from ...services.resume import run_phase
    ctx = contexts.resolve(body.context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    # The pick is remembered on the item BEFORE firing: the run reads it back from disk.
    if body.model:
        dev.set_work_item_model(dev_root, item_id, body.model)
    if body.effort:
        dev.set_work_item_effort(dev_root, item_id, body.effort)
    started, reason = run_phase(body.context_id, item_id)
    if not started:
        raise HTTPException(status_code=409, detail=reason)
    model = spine.effective_model(body.context_id, item_model=(item.get("model") or body.model))
    return {"ok": True, "status": "running", "id": item_id, "model": model}


@router.post("/dev/work-items/{item_id}/resume", response_model=PlanResponse)
async def dev_work_item_resume(item_id: str, body: PlanBody,
                               dev: DevKnowledgeService = Depends(get_dev),
                               spine: SystemSpine = Depends(get_spine)) -> dict:
    """RESUME a work-item whose run STOPPED: re-fire the phase's own background run.

    Nothing is rewound — the branch, worktree and artifacts stand, only the run is new. Distinct from
    Re-run, which throws the work away."""
    from ...services.resume import resume_item
    ctx = contexts.resolve(body.context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if body.model:
        dev.set_work_item_model(dev_root, item_id, body.model)
    if body.effort:
        dev.set_work_item_effort(dev_root, item_id, body.effort)
    started, reason = resume_item(body.context_id, item_id)
    if not started:
        raise HTTPException(status_code=409, detail=reason)
    model = spine.effective_model(body.context_id, item_model=(item.get("model") or body.model))
    return {"ok": True, "status": str(item.get("phase") or "active"), "id": item_id, "model": model}


@router.post("/dev/work-items/{item_id}/rerun", response_model=PlanResponse)
async def dev_work_item_rerun(item_id: str, body: PlanBody,
                              dev: DevKnowledgeService = Depends(get_dev),
                              spine: SystemSpine = Depends(get_spine)) -> dict:
    """RE-RUN: throw this item's work away and start it over in place. Destructive.

    Artifacts, reports, checkpoints and sessions are deleted and the worktree removed; the id, branch,
    run rows and graph relations survive."""
    from ...services.rerun import rerun_item
    ctx = contexts.resolve(body.context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    ok, reason = rerun_item(body.context_id, item_id)
    if not ok:
        raise HTTPException(status_code=409, detail=reason)
    fresh = dev.read_work_item(dev_root, item_id) or {}
    model = spine.effective_model(body.context_id, item_model=fresh.get("model"))
    return {"ok": True, "status": str(fresh.get("phase") or "active"), "id": item_id, "model": model}


class AuthorizeBody(BaseModel):
    context_id: str = "global"
    auth_id: str                    # the pending authorization request's id (from authorizations.md)
    decision: str                   # "granted" | "denied"


@router.post("/dev/work-items/{item_id}/authorize", response_model=PlanResponse)
async def dev_work_item_authorize(item_id: str, body: AuthorizeBody,
                                  dev: DevKnowledgeService = Depends(get_dev),
                                  spine: SystemSpine = Depends(get_spine)) -> dict:
    """The owner's grant or deny on a deferred authorization at review.

    Both RECORD and route nothing: the item stays at review so every request can be resolved in any
    order. `denied` also waives the blocked check. The owner grants unconditionally."""
    from ...services.loop import grant_authorization, deny_authorization
    ctx = contexts.resolve(body.context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    if dev.read_work_item(dev_root, item_id) is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if body.decision not in ("granted", "denied"):
        raise HTTPException(status_code=400, detail="decision must be 'granted' or 'denied'")
    if body.decision == "granted":
        ok, reason = grant_authorization(ctx, body.context_id, item_id, body.auth_id, by="owner")
    else:
        ok, reason = deny_authorization(ctx, body.context_id, item_id, body.auth_id, by="owner")
    if not ok:
        raise HTTPException(status_code=409, detail=reason)
    model = spine.effective_model(body.context_id)
    status = "granted" if body.decision == "granted" else "denied"
    return {"ok": True, "status": status, "id": item_id, "model": model}


@router.post("/dev/work-items/{item_id}/compact", response_model=PlanResponse)
async def dev_work_item_compact(item_id: str, body: PlanBody,
                                dev: DevKnowledgeService = Depends(get_dev),
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Compact NOW: run the full compaction sequence on this item's bound session — checkpoint first,
    then `/compact`, then the verdict.

    409 without a session or while a run is in flight, since the sequence takes the run-lock itself."""
    from ...services.compaction import run_compaction
    ctx = contexts.resolve(body.context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if item.get("done_at") or str(item.get("status")) == "done":
        raise HTTPException(status_code=409, detail="item is terminal")
    session_id = item.get("session_id")
    if not session_id:
        raise HTTPException(status_code=409, detail="item has no bound session to compact")
    if spine.is_item_running(body.context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is already in progress for this item")
    # Same precedence as every other run; `pre_pct` is None, because a manual fire has no trigger
    # reading.
    model = spine.effective_model(body.context_id, per_call=body.model, item_model=item.get("model"))
    asyncio.create_task(run_compaction(ctx, body.context_id, item_id, str(session_id),
                                       model=model, pre_pct=None))
    return {"ok": True, "status": "compacting", "id": item_id, "model": model}


class ScaffoldBody(BaseModel):
    context_id: str = "global"
    wave: str | None = None         # the roadmap wave this item instances (resolves its deliverable)
    deliverable: str | None = None  # …or a deliverable directly when no wave applies


@router.post("/dev/work-items/{item_id}/scaffold", response_model=WorkItemScaffoldResponse)
def dev_work_item_scaffold(item_id: str, body: ScaffoldBody,
                           dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Set a root work-item's anchor pointer — `wave` or `deliverable`. Pass one; the other clears to
    null."""
    dev_root = _dev_root(body.context_id)
    if not dev.read_work_item(dev_root, item_id):
        raise HTTPException(status_code=404, detail="work-item not found")
    dev.set_work_item_scaffold(dev_root, item_id, wave=body.wave, deliverable=body.deliverable)
    return {"ok": True, "id": item_id, "wave": body.wave, "deliverable": body.deliverable}


# Disposal is ONE act: abandon, the drilldown's Drop. `dev.delete_work_item` survives for the
# X-ray probe's teardown.


@router.get("/dev/work-items/{item_id}/detail", response_model=WorkItemDetailResponse,
            response_model_exclude_unset=True)
async def dev_work_item_detail(item_id: str, context_id: str = "global",
                               dev: DevKnowledgeService = Depends(get_dev),
                               spine: SystemSpine = Depends(get_spine)) -> dict:
    """A work-item's review payload: frontmatter and body, the rendered artifact content, the plan's
    `## Tasks` as a structured checklist, and the computed per-artifact status map."""
    ctx = contexts.resolve(context_id, "dev")
    dev_root = _dev_root(context_id)
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    # `read_work_item` returns raw frontmatter, so the enrich is what fills the live-run
    # telemetry.
    live_by_item = {r["item_id"]: r for r in spine.live_runs(context_id) if r.get("item_id")}
    dev.enrich_work_items(dev_root, [item], live_by_item, spine.run_stats(context_id, mode="dev"))
    item_dir = dev_root / "work-items" / item_id
    return {
        "item": item,
        "plan": dev.read_artifact_text(dev_root, item_id, "plan.md"),
        "prd": dev.read_artifact_text(dev_root, item_id, "prd.md"),
        "tasks": dev.read_tasks(dev_root, item_id),
        # The execution SNAPSHOT (present once the item is completed; live items use the rows).
        "execution": dev.read_artifact_text(dev_root, item_id, "execution.md"),
        "artifact_status": artifacts.artifact_status(item, item_dir, ctx.cwd),
        # The gate docs as raw text, rendered per-phase. This list tracks `_SPECS`, never a
        # remembered set.
        "docs": {name: dev.read_artifact_text(dev_root, item_id, artifacts.artifact_file(name))
                 for name in ("brief", "investigation")},
        # …and the continuity feed (newest-first checkpoint stubs).
        "checkpoints": artifacts.checkpoint_feed(item_dir),
    }


@router.post("/dev/work-items/{item_id}/seen", response_model=WorkItemSeenResponse)
async def dev_work_item_seen(item_id: str, context_id: str = "global",
                             dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Stamp the item as SEEN, clearing it from the attention engine's `unread` bucket. A read receipt:
    idempotent, never bumps `updated_at`."""
    dev_root = _dev_root(context_id)
    if dev.read_work_item(dev_root, item_id) is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    changed = dev.set_work_item_seen(dev_root, item_id)
    return {"ok": True, "id": item_id, "changed": changed}


@router.get("/dev/work-items/{item_id}/artifacts", response_model=WorkItemArtifactsResponse)
async def dev_work_item_artifacts(item_id: str, context_id: str = "global",
                                  spine: SystemSpine = Depends(get_spine)) -> dict:
    """The call-trail: every tool, sub-agent and skill this item's runs invoked, grouped by run.

    `runs` rides along so each group can say WHAT that run was — otherwise a header reads "Run #653"
    and the owner has to guess."""
    return {"artifacts": spine.events_for_item(context_id, item_id),
            "runs": spine.runs_for_item(context_id, item_id)}


@router.get("/dev/work-items/{item_id}/timeline", response_model=WorkItemTimelineResponse)
async def dev_work_item_timeline(item_id: str, context_id: str = "global") -> dict:
    """Every run of this item, oldest-first and phase-tagged with its ordered turn events — the
    read-only history the chat panel loads before live-streaming new frames."""
    return build_item_timeline(context_id, item_id)


@router.get("/dev/work-items/{item_id}/runs/{run_id}/input.html", response_class=HTMLResponse)
async def dev_work_item_run_input(item_id: str, run_id: int,
                                  context_id: str = "global") -> HTMLResponse:
    """The ACTUAL input a past run sent — the exact system prompt and body captured at send time — as a
    standalone HTML page. A friendly page renders when a run has no capture."""
    from ...services.input_preview import (build_captured_input, render_input_page,
                                           render_missing_input_page)
    data = build_captured_input(context_id, item_id, run_id)
    if data is None:
        return HTMLResponse(render_missing_input_page(item_id, run_id))
    return HTMLResponse(render_input_page(data))


@router.get("/dev/work-items/{item_id}/doc.html", response_class=HTMLResponse)
async def dev_work_item_doc(item_id: str, path: str, context_id: str = "global",
                            dev: DevKnowledgeService = Depends(get_dev)) -> HTMLResponse:
    """One of the item's AGENT-FACING artifacts as a standalone page.

    `path` is the report's own relative pointer; anything outside the item's `artifacts/` folder is
    refused as missing."""
    from ...services.doc_preview import (editable_artifact, render_doc_page,
                                         render_missing_doc_page, resolve_doc)
    item_dir = _dev_root(context_id) / "work-items" / item_id
    target = resolve_doc(item_dir, path)
    if target is None:
        return HTMLResponse(render_missing_doc_page(item_id, path), status_code=404)
    # Offering an edit button the PUT route would refuse is worse than not offering it.
    item = dev.read_work_item(_dev_root(context_id), item_id) or {}
    editable = (editable_artifact(item_dir, path) is not None
                and not status_router.is_terminal(item))
    return HTMLResponse(render_doc_page(item_id, path, target.read_text(),
                                        context_id=context_id, editable=editable))


class DocEditBody(BaseModel):
    context_id: str = "global"
    path: str            # the report's own relative pointer, e.g. "artifacts/plan.md"
    text: str


@router.put("/dev/work-items/{item_id}/doc", response_model=WorkItemDocEditResponse)
async def dev_work_item_doc_edit(item_id: str, body: DocEditBody,
                                 dev: DevKnowledgeService = Depends(get_dev),
                                 dev_store: DevStore = Depends(get_dev_store),
                                 spine: SystemSpine = Depends(get_spine)) -> dict:
    """The owner hand-edits `brief.md` or `plan.md` — the item's two statements of INTENT.

    Refused on a LIVE run, a TERMINAL item, or text failing the artifact's self-check. A save stamps
    `edited_by_owner`."""
    dev_root = _dev_root(body.context_id)
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if status_router.is_terminal(item):
        raise HTTPException(status_code=409,
                            detail="this item is finished — its plan records what was done, and "
                                   "editing it now would change the record, not the work")
    if spine.is_item_running(body.context_id, item_id):
        raise HTTPException(status_code=409,
                            detail="a run is in progress for this item — it may be writing this "
                                   "same file; wait for it to finish")
    item_dir = dev_root / "work-items" / item_id
    from ...services.doc_preview import editable_artifact
    artifact = editable_artifact(item_dir, body.path)
    if artifact is None:
        raise HTTPException(status_code=400,
                            detail=f"{body.path} is not owner-editable — only "
                                   f"{', '.join(artifacts.artifact_file(a) for a in artifacts.OWNER_EDITABLE)} "
                                   "state intent; the rest record what a run did")
    issues = artifacts.owner_edit(item_dir, artifact, body.text, item_kind=item.get("kind"))
    if issues:
        return {"ok": True, "id": item_id, "path": body.path, "saved": False, "issues": issues}
    text = (item_dir / "artifacts" / artifacts.artifact_file(artifact)).read_text()
    dev_store.log_event(body.context_id, "artifact.owner_edit",
                        f"You edited {artifacts.artifact_file(artifact)} by hand",
                        item_id=item_id, actor="owner",
                        meta={"artifact": artifact, "path": body.path})
    log.info("owner edited %s on %s", artifact, item_id)
    return {"ok": True, "id": item_id, "path": body.path, "saved": True, "issues": [],
            "edited_by_owner": artifacts.owner_edited_at(text)}


@router.post("/dev/prompt-extraction/run", response_model=PromptExtractionStatusResponse)
async def dev_prompt_extraction_run(context_id: str = "global") -> dict:
    """Fire a THROWAWAY prompt-extraction probe: a disposable work-item that runs the real lifecycle
    unattended to capture each phase's input, then tears itself down.

    One at a time per repo. Returns the current probe state."""
    from ...services import prompt_extraction as px
    return px.launch(context_id)


@router.get("/dev/prompt-extraction/status", response_model=PromptExtractionStatusResponse)
async def dev_prompt_extraction_status(context_id: str = "global") -> dict:
    """The repo's current probe state — whether one is running, and the captured input-page links for
    the last probe, which survive its teardown."""
    from ...services import prompt_extraction as px
    return px.status(context_id)


# An item goes terminal MECHANICALLY when its closing run reports; there is no owner promotion
# route.


# Phase sequencing is KIND-driven: `KIND_PROFILES` is the single source, with no route-local
# transition map.


# Config is chosen at capture and locked in at push, so there is no per-item reconfiguration
# route.


@router.post("/dev/work-items/{item_id}/advance", response_model=WorkItemAdvanceResponse)
async def dev_work_item_advance(item_id: str, context_id: str = "global",
                                dev: DevKnowledgeService = Depends(get_dev),
                                dev_store: DevStore = Depends(get_dev_store),
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Approve → advance a work-item to its kind's next phase.

    Refuses at the final phase, on a terminal item, or with a run in flight. The advance also rests
    the item `active`. The autopilot driver uses the same core."""
    ctx = contexts.resolve(context_id, "dev")
    return gates.advance_item(ctx, context_id, item_id, dev=dev, dev_store=dev_store,
                              spine=spine, actor="owner")


class AutopilotBody(BaseModel):
    on: bool


@router.post("/dev/work-items/{item_id}/autopilot", response_model=WorkItemAutopilotResponse)
async def dev_work_item_autopilot(item_id: str, body: AutopilotBody, context_id: str = "global",
                                  dev: DevKnowledgeService = Depends(get_dev),
                                  dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Enrol or un-enrol a work-item in autopilot — the per-item policy that drives its gates without a
    click.

    Allowed only PRE-BUILD: the last moment before code exists that flipping it is cheap."""
    dev_root = _dev_root(context_id)
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if str(item.get("phase")) not in ("triage", "plan"):
        raise HTTPException(status_code=409,
                            detail="autopilot can only be set pre-build (triage/plan)")
    changed = dev.set_work_item_autopilot(dev_root, item_id, body.on)
    if changed:
        dev_store.log_event(context_id, "item.autopilot",
                            f"Autopilot {'on' if body.on else 'off'}: {item.get('title') or item_id}",
                            item_id=item_id, actor="owner", meta={"on": body.on})
    return {"ok": True, "id": item_id, "autopilot": body.on, "changed": changed}
