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
from ....core import artifacts, gate_briefs, git_layer, kind_profiles, status_router
from ....gateway import contexts
from ...services.runs import DEFAULT_RUN_MODEL, _begin_run, _run_headless_plan, _render_execution_md
from ...services.learning import _fire_sweep_bg
from ...schemas.dev.work_items import (
    PlanResponse, WorkItemDeleteResponse, WorkItemDetailResponse, WorkItemArtifactsResponse,
    WorkItemCompleteResponse, WorkItemModelResponse, WorkItemEffortResponse, WorkItemAdvanceResponse,
    WorkItemScaffoldResponse, WorkItemSeenResponse,
)

log = logging.getLogger("superme-agent")

router = APIRouter()


# --- headless "Plan it": run /plan in the background, no chat -------------------
class PlanBody(BaseModel):
    context_id: str = "global"
    model: str | None = None   # per-run model choice; None -> DEFAULT_RUN_MODEL
    effort: str | None = None  # per-run reasoning effort; None -> item/repo/system default


@router.post("/dev/work-items/{item_id}/plan", response_model=PlanResponse)
async def dev_work_item_plan(item_id: str, body: PlanBody,
                             dev: DevKnowledgeService = Depends(get_dev),
                             spine: SystemSpine = Depends(get_spine)) -> dict:
    """Fire a headless /plan turn for a work-item — the "Plan it" quick-action. Opens the run
    immediately, then returns; the agent works in the background and the item lands at
    `awaiting_human` (the pre-main gate) when done. Poll GET /dev (`running`) for live state."""
    ctx = contexts.resolve(body.context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    item_dir = ctx.internal_root / "dev" / "work-items" / item_id
    if not (item_dir / "item.md").exists():
        raise HTTPException(status_code=404, detail="work-item not found")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id) or {}
    # Terminal guard: a headless plan on a done item would flip it back to active and end at
    # awaiting_human — resurrecting a closed item into the needs-you bucket.
    if item.get("done_at") or str(item.get("status")) == "done":
        raise HTTPException(status_code=409, detail="item is terminal")
    if body.model:
        dev.set_work_item_model(dev_root, item_id, body.model)  # remember the choice for later runs
    if body.effort:
        dev.set_work_item_effort(dev_root, item_id, body.effort)  # remember the choice for later runs
    # Same precedence as an interactive turn (session-model-precedence): explicit body pick → the
    # item's configured value → this repo's default → the system default.
    model = spine.effective_model(body.context_id, per_call=body.model, item_model=item.get("model"))
    effort = spine.effective_effort(body.context_id, per_call=body.effort, item_effort=item.get("effort"))
    # Atomic begin: opens the run, rests status at active, logs — or returns False (already running),
    # the per-item run-lock enforced at the data layer (no check-then-start window). 409 on contention.
    if not _begin_run(ctx, body.context_id, item_id, "plan", model, phase=item.get("phase")):
        raise HTTPException(status_code=409, detail="a run is already in progress for this item")
    asyncio.create_task(_run_headless_plan(ctx, body.context_id, item_id, item_dir, model, effort))
    return {"ok": True, "status": "planning", "id": item_id, "model": model}


@router.post("/dev/work-items/{item_id}/compact", response_model=PlanResponse)
async def dev_work_item_compact(item_id: str, body: PlanBody,
                                dev: DevKnowledgeService = Depends(get_dev),
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Compact NOW (S8, owner-fired): run the full compaction sequence on this item's bound
    session — checkpoint FIRST, then /compact, then the effectiveness verdict. The automatic
    trigger does the same on its own past `compaction_trigger_pct`; this is the manual handle
    (and how the gate test drives the machinery deterministically). 409 without a session or
    while a run is in flight (the sequence takes the item's run-lock itself)."""
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
    # Same precedence as every other run (explicit body pick → item → repo → system); pre_pct is
    # None — a manual fire has no trigger reading, and the verdict's calibration record keeps it so.
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
    """Set a root work-item's anchor pointer — `wave` (resolves its deliverable) or `deliverable`
    directly. Pass one; the other clears to null. 404 if the item is missing."""
    dev_root = _dev_root(body.context_id)
    if not dev.read_work_item(dev_root, item_id):
        raise HTTPException(status_code=404, detail="work-item not found")
    dev.set_work_item_scaffold(dev_root, item_id, wave=body.wave, deliverable=body.deliverable)
    return {"ok": True, "id": item_id, "wave": body.wave, "deliverable": body.deliverable}


@router.delete("/dev/work-items/{item_id}", response_model=WorkItemDeleteResponse)
async def dev_work_item_delete(item_id: str, context_id: str = "global",
                               dev: DevKnowledgeService = Depends(get_dev),
                               dev_store: DevStore = Depends(get_dev_store),
                               sessions: SessionStore = Depends(get_sessions),
                               spine: SystemSpine = Depends(get_spine)) -> dict:
    """Hard-delete a pre-build work-item and erase its trace: the `work-items/<id>/` folder,
    its SDK session transcript + index entry, and the originating inbox row. Only allowed while
    the item is in triage/plan (past that gate, code may have been touched). 409 otherwise."""
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if str(item.get("phase")) not in ("triage", "plan"):
        raise HTTPException(status_code=409, detail="only pre-build (triage/plan) items can be deleted")
    if spine.is_item_running(context_id, item_id):
        raise HTTPException(status_code=409,
                            detail="a run is in progress for this item — wait for it to finish")

    session_id = item.get("session_id")
    if session_id:
        sessions.delete(ctx, session_id, cause="deleted")  # hard delete; run trace preserved + labeled
    # Remove the inbox row this item was pushed from (routed_to == item_id), if any.
    inbox_removed = None
    for row in dev_store.list_inbox(context_id):
        if row.get("routed_to") == item_id:
            dev_store.delete_inbox(row["id"])
            inbox_removed = row["id"]
    # Typed-awaiting router (D2/D3): deleting a BLOCKING child must release its paused parent —
    # the resume normally fires on the child's terminal event, and a hard delete removes the
    # child before that event can ever exist (the parent would wedge at awaiting_child forever).
    all_items = dev.read_all(dev_root)["work_items"]
    deleted = dev.delete_work_item(dev_root, item_id)
    sf = item.get("spawned_from") or {}
    if isinstance(sf, dict) and sf.get("relation") == "blocking":
        for it in all_items:
            if it.get("id") == item_id:
                it["status"] = "done"   # gone counts as closed for the resume scan
        resume_id = status_router.parent_to_resume(all_items, item)
        if resume_id:
            dev.set_work_item_status(dev_root, resume_id, "active")
            dev_store.log_event(context_id, "item.resume",
                                f"Blocking child {item_id} deleted — parent resumed",
                                item_id=resume_id, actor="daemon", meta={"child": item_id})
    spine.release_item_runs(context_id, item_id)  # close any live run; KEEP the run history
    # The item's dev-activity events are historical trace → PRESERVED (never wiped). The item.drop
    # marker below records the deletion itself in the repo's activity log.
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
    the review popup shows — plan.md and prd.md as Markdown bodies, the plan's `## Tasks` as a
    structured `{text, done}` checklist, and the COMPUTED per-artifact status map (S2: derived
    from file existence + self-check + evidence freshness — never stored)."""
    ctx = contexts.resolve(context_id, "dev")
    dev_root = _dev_root(context_id)
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    item_dir = dev_root / "work-items" / item_id
    return {
        "item": item,
        "plan": dev.read_artifact_text(dev_root, item_id, "plan.md"),
        "prd": dev.read_artifact_text(dev_root, item_id, "prd.md"),
        "tasks": dev.read_tasks(dev_root, item_id),
        # The execution archive (present once the item is completed; live items use the rows).
        "execution": dev.read_artifact_text(dev_root, item_id, "execution.md"),
        "artifact_status": artifacts.artifact_status(item, item_dir, ctx.cwd),
        # S7 drilldown: the remaining gate docs as raw text (rendered per-phase sub-tab)…
        "docs": {name: dev.read_artifact_text(dev_root, item_id, artifacts.artifact_file(name))
                 for name in ("validation", "readiness", "findings", "closeout")},
        # …and the continuity feed (newest-first checkpoint stubs).
        "checkpoints": artifacts.checkpoint_feed(item_dir),
    }


@router.post("/dev/work-items/{item_id}/seen", response_model=WorkItemSeenResponse)
async def dev_work_item_seen(item_id: str, context_id: str = "global",
                             dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Stamp the item as SEEN (the owner opened its drilldown) — clears it from the attention
    engine's `unread` bucket (S7). A read receipt: idempotent, never bumps updated_at."""
    dev_root = _dev_root(context_id)
    if dev.read_work_item(dev_root, item_id) is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    changed = dev.set_work_item_seen(dev_root, item_id)
    return {"ok": True, "id": item_id, "changed": changed}


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
    """Complete + archive a close-phase work-item — the HUMAN promotion to terminal (D8: the
    agent never self-closes; this FE route has no agent-tool counterpart). Mechanically refused
    while any child (blocking/parallel spawned_from edge) is non-terminal (D3). Snapshots the
    execution trace to `artifacts/execution.md` (the folder persists), stamps status=done +
    outcome=completed + done_at, resumes an awaiting_child parent whose last blocking child this
    was (status router), then reclaims the SDK transcript + frees run rows. Events are kept."""
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if item.get("done_at") or str(item.get("status")) == "done":
        raise HTTPException(status_code=409, detail="already completed")
    if not kind_profiles.is_final_phase(item.get("kind"), item.get("phase") or "triage"):
        raise HTTPException(status_code=409, detail="only close-phase items can be completed")
    if spine.is_item_running(context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    # Close criteria (S6/D8, layer zero of the three-layer close protocol): the kind's declared
    # criteria evaluated MECHANICALLY — required artifacts clean, children terminal, closeout
    # claims ground-truth-verified, evidence fresh, merged-or-logged, knowledge row resolved.
    # Refusal is itemized (retry-shaped); nothing changes state.
    all_items = dev.read_all(dev_root)["work_items"]
    cr = gate_briefs.close_readiness(item, dev_root / "work-items" / item_id, dev_root,
                                     ctx.cwd, all_items)
    if not cr["ok"]:
        fails = "; ".join(f"{c['criterion']}: {c['detail']}"
                          for c in cr["checks"] if not c["ok"])
        raise HTTPException(status_code=409, detail=f"close criteria not met — {fails}")
    # 1. snapshot BEFORE freeing rows.
    md = _render_execution_md(context_id, item_id, item)
    dev.write_artifact(dev_root, item_id, "execution.md", md)
    # 2. terminal: status=done + outcome=completed + done_at (status change, never a delete).
    dev.set_work_item_terminal(dev_root, item_id, "completed")
    # 2a. S4 terminal git cleanup: remove the worktree DIR, KEEP the branch ref (near-free trace —
    #     never-delete holds; the record on the item stays too). Failure is surfaced, never silent,
    #     and never blocks completion (the item is done; a stray dir is a reconciliation concern).
    worktree_removed = None
    if item.get("git_worktree"):
        try:
            res = git_layer.remove_worktree(ctx.cwd, ctx.id, item_id)
            worktree_removed = bool(res["verified"])
        except (git_layer.GitError, git_layer.GitBusy) as e:
            worktree_removed = False
            log.warning("worktree cleanup failed for %s: %s", item_id, e)
    # 2b. typed-awaiting router: if this was the last open BLOCKING child of an awaiting_child
    #     parent, auto-resume the parent (no human involved — D2).
    for it in all_items:
        if it.get("id") == item_id:
            it["status"] = "done"
    resume_id = status_router.parent_to_resume(all_items, item or {"id": item_id})
    if resume_id:
        dev.set_work_item_status(dev_root, resume_id, "active")
        dev_store.log_event(context_id, "item.resume",
                            f"Blocking child {item_id} closed — parent resumed",
                            item_id=resume_id, actor="daemon", meta={"child": item_id})
    # 3. reclaim disk: release_item_runs is STATUS-ONLY (rows are permanent — never-delete-logs;
    #    it just closes any live row); the session transcript is reclaimed
    #    AFTER a final capture sweep (WI-8) — the sweep must read the transcript before it's purged,
    #    so the purge is chained behind the background sweep. When auto-learning is OFF we skip the
    #    sweep but STILL purge (disk reclamation is not a learning concern).
    session_id = item.get("session_id")
    if spine.learning_enabled_for(context_id):
        _fire_sweep_bg(ctx, session_id, then_delete="retired")
    elif session_id:
        sessions.delete(ctx, session_id, cause="retired")  # workflow done → retired
    runs_freed = spine.release_item_runs(context_id, item_id)
    dev_store.log_event(context_id, "item.complete",
                        f"Completed + archived: {item.get('title') or item_id}",
                        item_id=item_id, actor="owner", meta={"runs_freed": runs_freed})
    out = {"ok": True, "id": item_id, "archived": "artifacts/execution.md",
           "session_cleared": bool(session_id), "runs_freed": runs_freed}
    if worktree_removed is not None:
        out["worktree_removed"] = worktree_removed
    return out


# Phase sequencing is KIND-driven (workspace-workflow D1/D2): the kernel's KIND_PROFILES table is
# the single source of the per-kind pipeline — no route-local transition map.


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


class EffortBody(BaseModel):
    context_id: str = "global"
    effort: str


@router.post("/dev/work-items/{item_id}/effort", response_model=WorkItemEffortResponse)
async def dev_work_item_set_effort(item_id: str, body: EffortBody,
                                   dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Configure the reasoning effort a work-item's runs use (plan + bound chat) — reconfigurable
    anytime from the review popup. Persisted to `item.md` frontmatter."""
    dev_root = _dev_root(body.context_id)
    if dev.read_work_item(dev_root, item_id) is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    dev.set_work_item_effort(dev_root, item_id, body.effort)
    return {"ok": True, "id": item_id, "effort": body.effort}


@router.post("/dev/work-items/{item_id}/advance", response_model=WorkItemAdvanceResponse)
async def dev_work_item_advance(item_id: str, context_id: str = "global",
                                dev: DevKnowledgeService = Depends(get_dev),
                                dev_store: DevStore = Depends(get_dev_store),
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Approve → advance a work-item to its kind's next phase (the owner's gate; sequencing
    comes from KIND_PROFILES — triage→plan→… per kind). Refuses if the item is at its final
    phase, terminal, or a run is in flight. The gate decision also rests the item at `active`
    (an awaiting_human item just got its answer)."""
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if item.get("done_at") or str(item.get("status")) == "done":
        raise HTTPException(status_code=409, detail="item is terminal")
    if spine.is_item_running(context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    cur = str(item.get("phase") or "triage")
    try:
        nxt = kind_profiles.next_phase(item.get("kind"), cur)
    except KeyError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not nxt:
        raise HTTPException(status_code=409, detail=f"phase {cur} has no next phase")
    # S4 git layer: ENTERING build for a worktree kind creates the item's branch + worktree
    # TRANSACTIONALLY, BEFORE the phase flips — a failed git op leaves the item untouched at its
    # current phase (no half-state). Branch topology (D4): a blocking child branches FROM its
    # parent's branch; parallel/spawn (and originals) from the trunk. Research: no-op.
    git_record = None
    profile = kind_profiles.get_profile(item.get("kind"))
    # D6 validation-at-consumption: the pre-main gate CONSUMES plan.md — entering the working
    # phase (build/investigate) without a clean plan means approving a plan that doesn't exist.
    if nxt in ("build", "investigate"):
        item_dir = ctx.internal_root / "dev" / "work-items" / item_id
        plan_issues = artifacts.self_check(item_dir, "plan", item_kind=item.get("kind"))
        if plan_issues:
            raise HTTPException(status_code=409,
                                detail="plan.md isn't gate-ready — " + "; ".join(plan_issues[:3]))
    if nxt == "build" and profile.worktree and not item.get("git_worktree"):
        base = None
        sf = item.get("spawned_from") or {}
        if isinstance(sf, dict) and sf.get("relation") == "blocking":
            base = (dev.read_work_item(dev_root, str(sf.get("item"))) or {}).get("git_branch")
        try:
            git_record = git_layer.create_worktree(ctx.cwd, ctx.id, item_id,
                                                   item.get("title") or "", base=base)
        except (git_layer.GitError, git_layer.GitBusy) as e:
            raise HTTPException(status_code=409, detail=f"worktree create failed: {e}")
        dev.set_work_item_git(dev_root, item_id, git_branch=git_record["branch"],
                              git_worktree=git_record["worktree"], git_base=git_record["base"])
        dev_store.log_event(context_id, "git.worktree",
                            f"Created worktree on branch {git_record['branch']}",
                            item_id=item_id, actor="daemon", meta=git_record)
    dev.set_work_item_phase(dev_root, item_id, nxt)
    if str(item.get("status")) == "awaiting_human":
        dev.set_work_item_status(dev_root, item_id, "active")
    # The approval gate — item-scoped. PRD §4.9.
    dev_store.log_event(context_id, "phase.advance",
                        f"Approved {cur} → {nxt}: {item.get('title') or item_id}",
                        item_id=item_id, actor="owner", meta={"from": cur, "to": nxt})
    # Capture trigger (WI-8): a phase just closed — sweep the bound session for learnings the
    # finished phase produced. Background + watermarked, so the gate stays instant and idempotent.
    # Gated by the auto-learning master switch (off by default — token safety).
    if spine.learning_enabled_for(context_id):
        _fire_sweep_bg(ctx, item.get("session_id"))
    log.info("advanced work-item %s: %s → %s", item_id, cur, nxt)
    out = {"ok": True, "id": item_id, "phase": nxt, "from": cur}
    if git_record:
        out["git"] = git_record
    return out
