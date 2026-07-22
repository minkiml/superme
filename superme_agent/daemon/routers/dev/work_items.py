"""Work-item lifecycle routes: the plan/design → build/eval → done pipeline + its background "Plan it"
quick-action, review payload, call-trail, model config, phase-advance gate, and complete/archive.

The orchestration lives in services/ (run lifecycle in runs.py, the capture-sweep triggers in
learning.py); these routes are the thin HTTP layer that calls them.
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
from ....core import artifacts, gate_briefs, git_layer, kind_profiles, status_router
from ....gateway import contexts
from ...services.runs import (
    DEFAULT_RUN_MODEL, _begin_run, _run_background_plan, _render_execution_md, build_item_timeline,
)
from ...services.learning import _fire_sweep_bg
from ...services import scheduler, gates
from ...schemas.dev.work_items import (
    PlanResponse, WorkItemDeleteResponse, WorkItemDetailResponse, WorkItemArtifactsResponse,
    WorkItemCompleteResponse, WorkItemAdvanceResponse,
    WorkItemScaffoldResponse, WorkItemSeenResponse, WorkItemAutopilotResponse,
    WorkItemTimelineResponse,
)

log = logging.getLogger("superme-agent")

router = APIRouter()


# --- background "Plan it": run /plan with no chat surface -------------------
class PlanBody(BaseModel):
    context_id: str = "global"
    model: str | None = None   # per-run model choice; None -> DEFAULT_RUN_MODEL
    effort: str | None = None  # per-run reasoning effort; None -> item/repo/system default


@router.post("/dev/work-items/{item_id}/plan", response_model=PlanResponse)
async def dev_work_item_plan(item_id: str, body: PlanBody,
                             dev: DevKnowledgeService = Depends(get_dev),
                             spine: SystemSpine = Depends(get_spine)) -> dict:
    """Fire a background /plan turn for a work-item — the "Plan it" quick-action. Opens the run
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
    # Terminal guard: a background plan on a done item would flip it back to active and end at
    # awaiting_human — resurrecting a closed item into the needs-you bucket.
    if item.get("done_at") or str(item.get("status")) == "done":
        raise HTTPException(status_code=409, detail="item is terminal")
    # Phase guard (playground-e2e-blockers): a plan run only makes sense in the `plan` phase. Firing
    # it in `triage` burned ~32k tokens on a plan skill that self-blocks (item not yet triaged) and
    # mislabeled the waste as a triage run. Refuse BEFORE opening the run — triage happens in chat
    # (auto-triage on push is #120), not via this button.
    if str(item.get("phase")) != "plan":
        raise HTTPException(
            status_code=409,
            detail=f"planning runs in the `plan` phase — this item is in `{item.get('phase')}`. "
                   "Triage it first (in chat); the plan run is available once it reaches plan.")
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
    asyncio.create_task(_run_background_plan(ctx, body.context_id, item_id, item_dir, model, effort))
    return {"ok": True, "status": "planning", "id": item_id, "model": model}


@router.post("/dev/work-items/{item_id}/vet", response_model=PlanResponse)
async def dev_work_item_vet(item_id: str, body: PlanBody,
                            dev: DevKnowledgeService = Depends(get_dev),
                            spine: SystemSpine = Depends(get_spine)) -> dict:
    """Run VET on demand and let the loop take over (build-vet-loop §5): fire a background vet run
    on this item's worktree. From there the daemon-side driver self-drives — passed → review ·
    failed → a build cycle handed the vet report (while `loop_autorun` and the breakers allow) ·
    stale → re-vet · unverified → fail closed. This is the owner's manual "vet what's built now"
    action; the AUTONOMOUS loop opens build-first (gates.enter_build_loop), so vet is the loop's
    sole DECISION point, not its entry. 409 when the item isn't a runnable vet-phase item or a run
    is in flight."""
    from ...services.loop import start_vet_run
    ctx = contexts.resolve(body.context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if body.model:
        dev.set_work_item_model(dev_root, item_id, body.model)    # remembered for later runs
    if body.effort:
        dev.set_work_item_effort(dev_root, item_id, body.effort)
    started, reason = start_vet_run(ctx, body.context_id, item_id)
    if not started:
        raise HTTPException(status_code=409, detail=reason)
    model = spine.effective_model(body.context_id, item_model=(item.get("model") or body.model))
    return {"ok": True, "status": "vetting", "id": item_id, "model": model}


@router.post("/dev/work-items/{item_id}/continue", response_model=PlanResponse)
async def dev_work_item_continue(item_id: str, body: PlanBody,
                                 dev: DevKnowledgeService = Depends(get_dev),
                                 spine: SystemSpine = Depends(get_spine)) -> dict:
    """The owner's CONTINUE on a build parked at a human gate (BV-A1): RE-ENTER the build⟷vet loop.
    Resumes the item's build thread to finalize — complete what's doable, record any wall it can't
    pass itself as an assumption — then the normal build→vet→review flow carries the gap to review.
    This is distinct from a bare chat reply to a paused build (which runs a `chat` turn and does NOT
    advance the loop). 409 when the item isn't a parked build-phase item with a live worktree, or a
    run is in flight."""
    from ...services.loop import start_continue_build
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
    started, reason = start_continue_build(ctx, body.context_id, item_id)
    if not started:
        raise HTTPException(status_code=409, detail=reason)
    model = spine.effective_model(body.context_id, item_model=(item.get("model") or body.model))
    return {"ok": True, "status": "building", "id": item_id, "model": model}


class AuthorizeBody(BaseModel):
    context_id: str = "global"
    auth_id: str                    # the pending authorization request's id (from authorizations.md)
    decision: str                   # "granted" | "denied"


@router.post("/dev/work-items/{item_id}/authorize", response_model=PlanResponse)
async def dev_work_item_authorize(item_id: str, body: AuthorizeBody,
                                  dev: DevKnowledgeService = Depends(get_dev),
                                  spine: SystemSpine = Depends(get_spine)) -> dict:
    """The owner's grant/deny on a deferred authorization at review (BV-A2.3). `granted` records the
    grant and routes the item back to build to perform the now-allowed change; `denied` accepts the
    gap (the blocked check is waived) and leaves the item at review for the owner's approve. Unlike
    the deputy, the owner grants unconditionally — the delegated-authority floor only binds the
    deputy. 409 when the item/request isn't in a grantable state or a run is in flight."""
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
    status = "building" if body.decision == "granted" else "denied"
    return {"ok": True, "status": status, "id": item_id, "model": model}


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

    session_ids = dev.work_item_session_ids(item)   # ALL role threads (intake/build/vet + legacy)
    for sid in session_ids:
        sessions.delete(ctx, sid, cause="deleted")  # hard delete; run trace preserved + labeled
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
    # A deleted upstream releases its peers too — a hard delete removes the item before any
    # terminal event can fire, so without this the downstream parks forever on an id that no
    # longer exists (the same wedge the blocking-child block above guards).
    all_items = [it for it in all_items if it.get("id") != item_id]
    scheduler.release_downstream(dev, dev_root, dev_store, context_id, all_items, item_id,
                                 cause="deleted")
    gates.pump_autopilot_slots(context_id)
    spine.release_item_runs(context_id, item_id)  # close any live run; KEEP the run history
    # The item's dev-activity events are historical trace → PRESERVED (never wiped). The item.drop
    # marker below records the deletion itself in the repo's activity log.
    dev_store.log_event(context_id, "item.drop",
                        f"Deleted work-item: {item.get('title') or item_id}",
                        actor="owner", meta={"item_id": item_id})
    log.info("deleted work-item %s (sessions=%d, inbox=%s)", item_id, len(session_ids), inbox_removed)
    return {"ok": deleted, "id": item_id, "session_cleared": bool(session_ids), "inbox_removed": inbox_removed}


@router.get("/dev/work-items/{item_id}/detail", response_model=WorkItemDetailResponse,
            response_model_exclude_unset=True)
async def dev_work_item_detail(item_id: str, context_id: str = "global",
                               dev: DevKnowledgeService = Depends(get_dev),
                               spine: SystemSpine = Depends(get_spine)) -> dict:
    """A work-item's review payload: its frontmatter/body plus the rendered artifact content
    the review popup shows — plan.md and prd.md as Markdown bodies, the plan's `## Tasks` as a
    structured `{text, done}` checklist, and the COMPUTED per-artifact status map (S2: derived
    from file existence + self-check + evidence freshness — never stored)."""
    ctx = contexts.resolve(context_id, "dev")
    dev_root = _dev_root(context_id)
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    # Decorate with the SAME live-run telemetry the board carries (running · run_tokens · run_ctx_pct
    # · accumulated tokens), so a drilldown poll drives the chat's live "thinking… · Ns · N tokens"
    # indicator. `read_work_item` returns raw frontmatter only — the enrich is what fills these.
    live_by_item = {r["item_id"]: r for r in spine.live_runs(context_id) if r.get("item_id")}
    dev.enrich_work_items(dev_root, [item], live_by_item, spine.run_stats(context_id, mode="dev"))
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


@router.get("/dev/work-items/{item_id}/timeline", response_model=WorkItemTimelineResponse)
async def dev_work_item_timeline(item_id: str, context_id: str = "global") -> dict:
    """F2 unified timeline: every run of this item, oldest-first, phase/role-tagged with its ordered
    turn events (prompt · reply · calls) — the read-only history the chat panel loads before
    live-streaming new frames from the item's event broker. All phases in one chronological view."""
    return build_item_timeline(context_id, item_id)


@router.get("/dev/work-items/{item_id}/phases/{phase}/preview-input.html",
            response_class=HTMLResponse)
async def dev_work_item_preview_input(item_id: str, phase: str,
                                      context_id: str = "global") -> HTMLResponse:
    """Prompt inspector (B): the FULL input a fresh `phase` run would receive — system prompt +
    prompt body — as a standalone HTML page for a new browser tab. Reconstructed from the item's
    current state through the live assembly code (faithful by construction); fires nothing."""
    from ...services.input_preview import build_preview_input, render_input_page
    data = build_preview_input(context_id, item_id, phase)
    if data is None:
        raise HTTPException(status_code=404, detail="work-item or phase not found")
    return HTMLResponse(render_input_page(data, mode="preview"))


@router.get("/dev/work-items/{item_id}/runs/{run_id}/input.html", response_class=HTMLResponse)
async def dev_work_item_run_input(item_id: str, run_id: int,
                                  context_id: str = "global") -> HTMLResponse:
    """Prompt inspector (A): the ACTUAL input a past run sent — the exact system prompt + prompt
    body captured at send time — as a standalone HTML page. A friendly page renders when a run has
    no capture (a pre-feature run, or a chat/deputy turn)."""
    from ...services.input_preview import (build_captured_input, render_input_page,
                                           render_missing_input_page)
    data = build_captured_input(context_id, item_id, run_id)
    if data is None:
        return HTMLResponse(render_missing_input_page(item_id, run_id))
    return HTMLResponse(render_input_page(data, mode="captured"))


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
    # 2c. peer scheduler: release every item parked at `awaiting_upstream` on this one (the
    #     `after:` edge). Only a COMPLETED upstream releases — see services/scheduler.py.
    for it in all_items:
        if it.get("id") == item_id:
            it["outcome"] = "completed"
    scheduler.release_downstream(dev, dev_root, dev_store, context_id, all_items, item_id,
                                 cause="completed")
    # An item completing from build⟷vet frees an autopilot slot — pump the queue.
    gates.pump_autopilot_slots(context_id)
    # 3. reclaim disk: release_item_runs is STATUS-ONLY (rows are permanent — never-delete-logs;
    #    it just closes any live row); the session transcript is reclaimed
    #    AFTER a final capture sweep (WI-8) — the sweep must read the transcript before it's purged,
    #    so the purge is chained behind the background sweep. When auto-learning is OFF we skip the
    #    sweep but STILL purge (disk reclamation is not a learning concern).
    session_ids = dev.work_item_session_ids(item)   # ALL role threads (intake/build/vet + legacy)
    for sid in session_ids:
        if spine.learning_enabled_for(context_id):
            _fire_sweep_bg(ctx, sid, then_delete="retired")
        else:
            sessions.delete(ctx, sid, cause="retired")  # workflow done → retired
    runs_freed = spine.release_item_runs(context_id, item_id)
    dev_store.log_event(context_id, "item.complete",
                        f"Completed + archived: {item.get('title') or item_id}",
                        item_id=item_id, actor="owner", meta={"runs_freed": runs_freed})
    out = {"ok": True, "id": item_id, "archived": "artifacts/execution.md",
           "session_cleared": bool(session_ids), "runs_freed": runs_freed}
    if worktree_removed is not None:
        out["worktree_removed"] = worktree_removed
    return out


# Phase sequencing is KIND-driven (workspace-workflow D1/D2): the kernel's KIND_PROFILES table is
# the single source of the per-kind pipeline — no route-local transition map.


# F3: per-work-item model/effort override routes REMOVED — config is chosen at capture (inbox row)
# and LOCKED IN at push (inbox_flow sets item.model/effort once); no reconfiguration after. The
# `set_work_item_model`/`set_work_item_effort` core methods survive as the push-time writers.


@router.post("/dev/work-items/{item_id}/advance", response_model=WorkItemAdvanceResponse)
async def dev_work_item_advance(item_id: str, context_id: str = "global",
                                dev: DevKnowledgeService = Depends(get_dev),
                                dev_store: DevStore = Depends(get_dev_store),
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Approve → advance a work-item to its kind's next phase (the owner's gate; sequencing
    comes from KIND_PROFILES — triage→plan→… per kind). Refuses if the item is at its final
    phase, terminal, or a run is in flight. The gate decision also rests the item at `active`
    (an awaiting_human item just got its answer). Owner-driven, so `ratify=True` — approving
    ratifies the brief's assumptions; the autopilot driver uses the same core with ratify=False."""
    ctx = contexts.resolve(context_id, "dev")
    return gates.advance_item(ctx, context_id, item_id, dev=dev, dev_store=dev_store,
                              spine=spine, ratify=True, actor="owner")


class AutopilotBody(BaseModel):
    on: bool


@router.post("/dev/work-items/{item_id}/autopilot", response_model=WorkItemAutopilotResponse)
async def dev_work_item_autopilot(item_id: str, body: AutopilotBody, context_id: str = "global",
                                  dev: DevKnowledgeService = Depends(get_dev),
                                  dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Enrol / un-enrol a work-item in autopilot — the per-item policy that drives its gates without
    a click. Allowed only PRE-BUILD (phase in triage/plan): the inbox-stage decision, and the last
    moment before code exists that flipping it is cheap. 409 past that. The flag is durable; the
    driver (services/gates.maybe_autopilot_advance) reads it when a run rests the item at a gate."""
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
