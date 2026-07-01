"""Work-item lifecycle routes: the plan/design → build/eval → done pipeline + its headless "Plan it"
quick-action, review payload, call-trail, model config, phase-advance gate, and complete/archive.

The orchestration lives in services/ (run lifecycle in runs.py, the capture-sweep triggers in
learning.py); these routes are the thin HTTP layer that calls them.
"""

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...app_state import (
    DevKnowledgeService, DevStore, SessionStore, SystemSpine,
    get_dev, get_dev_store, get_sessions, get_spine,
)
from ...deps import dev_root as _dev_root
from ....gateway import contexts
from ...services.runs import DEFAULT_RUN_MODEL, _begin_run, _run_headless_plan, _render_execution_md
from ...services.learning import _fire_sweep_bg
from ...schemas.dev.work_items import (
    PlanResponse, WorkItemDeleteResponse, WorkItemDetailResponse, WorkItemArtifactsResponse,
    WorkItemCompleteResponse, WorkItemModelResponse, WorkItemAdvanceResponse,
)

log = logging.getLogger("superme-agent")

router = APIRouter()


# --- headless "Plan it": run /plan in the background, no chat -------------------
class PlanBody(BaseModel):
    context_id: str = "global"
    model: str | None = None   # per-run model choice; None -> DEFAULT_RUN_MODEL


@router.post("/dev/work-items/{item_id}/plan", response_model=PlanResponse)
async def dev_work_item_plan(item_id: str, body: PlanBody,
                             dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Fire a headless /plan turn for a work-item — the "Plan it" quick-action. Flips the
    item to in_progress immediately, then returns; the agent works in the background and the
    item lands at `waiting` when done. Poll GET /dev (`running`) for the live planning state."""
    ctx = contexts.resolve(body.context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    item_dir = ctx.internal_root / "dev" / "work-items" / item_id
    if not (item_dir / "item.md").exists():
        raise HTTPException(status_code=404, detail="work-item not found")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id) or {}
    model = body.model or item.get("model") or DEFAULT_RUN_MODEL
    if body.model:
        dev.set_work_item_model(dev_root, item_id, body.model)  # remember the choice for later runs
    # Atomic begin: opens the run, flips to in_progress, logs — or returns False (already running),
    # the per-item run-lock enforced at the data layer (no check-then-start window). 409 on contention.
    if not _begin_run(ctx, body.context_id, item_id, "plan", model):
        raise HTTPException(status_code=409, detail="a run is already in progress for this item")
    asyncio.create_task(_run_headless_plan(ctx, body.context_id, item_id, item_dir, model))
    return {"ok": True, "status": "planning", "work_item_id": item_id, "model": model}


@router.delete("/dev/work-items/{item_id}", response_model=WorkItemDeleteResponse)
async def dev_work_item_delete(item_id: str, context_id: str = "global",
                               dev: DevKnowledgeService = Depends(get_dev),
                               dev_store: DevStore = Depends(get_dev_store),
                               sessions: SessionStore = Depends(get_sessions),
                               spine: SystemSpine = Depends(get_spine)) -> dict:
    """Hard-delete a plan/design work-item and erase its trace: the `work-items/<id>/` folder,
    its SDK session transcript + index entry, and the originating inbox row. Only allowed while
    the item is in plan_design (past that gate, code may have been touched). 409 otherwise."""
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if str(item.get("phase")) != "plan_design":
        raise HTTPException(status_code=409, detail="only plan/design items can be deleted")

    session_id = item.get("session_id")
    if session_id:
        sessions.purge(ctx, session_id)
    # Remove the inbox row this item was pushed from (routed_to == item_id), if any.
    inbox_removed = None
    for row in dev_store.list_inbox(context_id):
        if row.get("routed_to") == item_id:
            dev_store.delete_inbox(row["id"])
            inbox_removed = row["id"]
    deleted = dev.delete_work_item(dev_root, item_id)
    spine.delete_item_runs(context_id, item_id)   # drop this item's runs (live + historical)
    dev_store.delete_events(context_id, item_id)  # wipe the item's own events
    # Record the drop as a DEV-NATIVE event (item_id=None) so it survives the wipe above and
    # stays visible in the repo's activity log. PRD §4.9.
    dev_store.log_event(context_id, "item.drop",
                        f"Deleted work-item: {item.get('title') or item_id}",
                        actor="owner", meta={"item_id": item_id})
    log.info("deleted work-item %s (session=%s, inbox=%s)", item_id, bool(session_id), inbox_removed)
    return {"ok": deleted, "id": item_id, "session_cleared": bool(session_id), "inbox_removed": inbox_removed}


@router.get("/dev/work-items/{item_id}/detail", response_model=WorkItemDetailResponse,
            response_model_exclude_unset=True)
async def dev_work_item_detail(item_id: str, context_id: str = "global",
                               dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """A work-item's review payload: its frontmatter/body plus the rendered artifact content
    the review popup shows — plan.md and prd.md as Markdown bodies, tasks.md as a structured
    `{text, done}` checklist. Structured render, not a raw file dump."""
    dev_root = _dev_root(context_id)
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    return {
        "item": item,
        "plan": dev.read_artifact_text(dev_root, item_id, "plan.md"),
        "prd": dev.read_artifact_text(dev_root, item_id, "prd.md"),
        "tasks": dev.read_tasks(dev_root, item_id),
        # The execution archive (present once the item is completed; live items use the rows).
        "execution": dev.read_artifact_text(dev_root, item_id, "execution.md"),
    }


@router.get("/dev/work-items/{item_id}/artifacts", response_model=WorkItemArtifactsResponse)
async def dev_work_item_artifacts(item_id: str, context_id: str = "global",
                                  spine: SystemSpine = Depends(get_spine)) -> dict:
    """The call-trail: every tool / sub-agent / skill this work-item's runs invoked, grouped by
    run (newest run first, calls in order within a run). Powers the detail popup's Execution tab."""
    return {"artifacts": spine.artifacts_for_item(context_id, item_id)}


@router.post("/dev/work-items/{item_id}/complete", response_model=WorkItemCompleteResponse)
async def dev_work_item_complete(item_id: str, context_id: str = "global",
                                 dev: DevKnowledgeService = Depends(get_dev),
                                 dev_store: DevStore = Depends(get_dev_store),
                                 sessions: SessionStore = Depends(get_sessions),
                                 spine: SystemSpine = Depends(get_spine)) -> dict:
    """Complete + archive a Done-phase work-item (the tick-out). Snapshots the execution trace to
    `artifacts/execution.md` (the folder persists), stamps `done_at`, then RECLAIMS disk by purging
    the SDK session transcript and freeing the item's run + run_artifact rows. Events are kept."""
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if item.get("done_at"):
        raise HTTPException(status_code=409, detail="already completed")
    if str(item.get("phase")) != "done":
        raise HTTPException(status_code=409, detail="only Done-phase items can be completed")
    if spine.is_item_running(context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    # 1. snapshot BEFORE freeing rows.
    md = _render_execution_md(context_id, item_id, item)
    dev.write_artifact(dev_root, item_id, "execution.md", md)
    # 2. mark complete (done_at).
    dev.set_work_item_done(dev_root, item_id)
    # 3. reclaim disk: the run/run_artifact rows are freed now; the session transcript is reclaimed
    #    AFTER a final capture sweep (WI-8) — the sweep must read the transcript before it's purged,
    #    so the purge is chained behind the background sweep. When auto-learning is OFF we skip the
    #    sweep but STILL purge (disk reclamation is not a learning concern).
    session_id = item.get("session_id")
    if spine.get_learning_enabled():
        _fire_sweep_bg(ctx, session_id, then_purge=True)
    elif session_id:
        sessions.purge(ctx, session_id)
    runs_freed = spine.delete_item_runs(context_id, item_id)
    dev_store.log_event(context_id, "item.complete",
                        f"Completed + archived: {item.get('title') or item_id}",
                        item_id=item_id, actor="owner", meta={"runs_freed": runs_freed})
    return {"ok": True, "id": item_id, "archived": "artifacts/execution.md",
            "session_cleared": bool(session_id), "runs_freed": runs_freed}


# The plan-phase forward gate: approving a plan_design item advances it to build_eval.
_PHASE_NEXT = {"plan_design": "build_eval", "build_eval": "done"}


class ModelBody(BaseModel):
    context_id: str = "global"
    model: str


@router.post("/dev/work-items/{item_id}/model", response_model=WorkItemModelResponse)
async def dev_work_item_set_model(item_id: str, body: ModelBody,
                                  dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Configure the model a work-item's runs use (plan + bound chat) — reconfigurable anytime
    from the review popup. Persisted to `item.md` frontmatter."""
    dev_root = _dev_root(body.context_id)
    if dev.read_work_item(dev_root, item_id) is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    dev.set_work_item_model(dev_root, item_id, body.model)
    return {"ok": True, "id": item_id, "model": body.model}


@router.post("/dev/work-items/{item_id}/advance", response_model=WorkItemAdvanceResponse)
async def dev_work_item_advance(item_id: str, context_id: str = "global",
                                dev: DevKnowledgeService = Depends(get_dev),
                                dev_store: DevStore = Depends(get_dev_store),
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Approve → advance a work-item to the next phase (the owner's gate). plan_design →
    build_eval today (approving the plan). Refuses if there's no next phase or a run is in
    flight on the item. Status/run-state is untouched — phase is the owner axis."""
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if spine.is_item_running(context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    cur = str(item.get("phase") or "plan_design")
    nxt = _PHASE_NEXT.get(cur)
    if not nxt:
        raise HTTPException(status_code=409, detail=f"phase {cur} has no next phase")
    dev.set_work_item_phase(dev_root, item_id, nxt)
    # The approval gate — item-scoped. PRD §4.9.
    dev_store.log_event(context_id, "phase.advance",
                        f"Approved {cur} → {nxt}: {item.get('title') or item_id}",
                        item_id=item_id, actor="owner", meta={"from": cur, "to": nxt})
    # Capture trigger (WI-8): a phase just closed — sweep the bound session for learnings the
    # finished phase produced. Background + watermarked, so the gate stays instant and idempotent.
    # Gated by the auto-learning master switch (off by default — token safety).
    if spine.get_learning_enabled():
        _fire_sweep_bg(ctx, item.get("session_id"))
    log.info("advanced work-item %s: %s → %s", item_id, cur, nxt)
    return {"ok": True, "id": item_id, "phase": nxt, "from": cur}
