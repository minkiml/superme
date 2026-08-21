"""The work-item drilldown's payload, its per-phase report reads, and the human-only abandon path.

This layer only GATHERS inputs; the payload is assembled in `services/drilldown`, so the drilldown
and the deputy read one computation of a gate's checks.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ...app_state import (
    DevKnowledgeService, DevStore, SessionStore, SystemSpine,
    get_dev, get_dev_store, get_sessions, get_spine,
)
from ....core import artifacts, git_layer, status_router
from ....core.artifacts import _atomic_write
from ...services import drilldown, git_ops, scheduler
from ....gateway import contexts
from ...schemas.dev.gates import (AbandonBody, AbandonResponse, DrilldownResponse, OwnerInputBody,
                                  OwnerInputResponse, OwnerNoteBody, OwnerReferenceBody,
                                  PhaseReportResponse)

log = logging.getLogger("superme-agent")

router = APIRouter()


def _load(context_id: str, item_id: str, dev: DevKnowledgeService):
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    return ctx, dev_root, item


@router.get("/dev/work-items/{item_id}/report/{phase}", response_model=PhaseReportResponse)
async def dev_work_item_report(item_id: str, phase: str, context_id: str = "global",
                               dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """One phase's user-facing report for the Reports tab: the markdown 1:1, plus the path
    to the agent-facing contract behind it. 404 when that phase wrote none — the tab greys
    itself from the drilldown rather than probing."""
    _ctx, dev_root, _item = _load(context_id, item_id, dev)
    report = artifacts.report_text(dev_root / "work-items" / item_id, phase)
    if report is None:
        raise HTTPException(status_code=404, detail=f"no report-{phase}.md for this item")
    return report


@router.get("/dev/work-items/{item_id}/from-you", response_model=OwnerInputResponse)
async def dev_work_item_owner_input(item_id: str, context_id: str = "global",
                                    dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """What the owner has written into the one section of the item that is theirs.

    Never 404s on a missing brief: `exists: false` tells the editor triage has not written one yet,
    which is not a broken read."""
    _ctx, dev_root, _item = _load(context_id, item_id, dev)
    return artifacts.owner_input(dev_root / "work-items" / item_id)


@router.put("/dev/work-items/{item_id}/from-you", response_model=OwnerInputResponse)
async def dev_work_item_set_owner_input(item_id: str, body: OwnerInputBody,
                                        dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Save the owner's own section, replacing it whole and leaving the rest untouched.

    HUMAN-ONLY: there is no agent tool behind it, because the value of the section is that an agent did
    not write it."""
    _ctx, dev_root, _item = _load(body.context_id, item_id, dev)
    try:
        return artifacts.write_owner_input(
            dev_root / "work-items" / item_id,
            references=[r.model_dump() for r in body.references],
            notes=[n.model_dump() for n in body.notes])
    except FileNotFoundError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/dev/work-items/{item_id}/drilldown", response_model=DrilldownResponse)
async def dev_work_item_drilldown(item_id: str, context_id: str = "global",
                                  dev: DevKnowledgeService = Depends(get_dev),
                                  dev_store: DevStore = Depends(get_dev_store),
                                  spine: SystemSpine = Depends(get_spine)) -> dict:
    """Everything the drilldown renders, computed once.

    Server-computed activation is the point: a greying rule that lives in the component sits beside the
    rule the backend actually enforces."""
    ctx, dev_root, item = _load(context_id, item_id, dev)
    # Without the enrich a working agent renders as idle: the Now strip reads `running`.
    live_by_item = {r["item_id"]: r for r in spine.live_runs(context_id) if r.get("item_id")}
    dev.enrich_work_items(dev_root, [item], live_by_item, spine.run_stats(context_id, mode="dev"))
    events = dev_store.list_events(context_id, item_id=item_id, limit=100)
    # The landing rule is a REPO fact, read unconditionally: it must not ride a nullable carrier
    # that answers another question.
    review_mode = git_ops.repo_review_mode(ctx, spine)
    git_health = None
    if item.get("git_branch") or item.get("git_worktree"):
        try:
            git_health = git_layer.worktree_health(ctx.cwd, ctx.id, item_id, item.get("git_branch"),
                                                   trunk=git_ops.repo_anchor(ctx, spine),
                                                   merge_commit=item.get("git_merge_commit"))
        except (git_layer.GitError, git_layer.GitBusy):
            git_health = None
    all_items = dev.read_all(dev_root)["work_items"]
    # Who raised this, from the inbox row's own `origin`. An absent row reads as owner.
    inbox_origin = ""
    if item.get("inbox_id"):
        row = dev_store.get_inbox(int(item["inbox_id"])) or {}
        inbox_origin = "agent" if "agent" in (row.get("origin") or []) else "user"
    # Read in ONE place the deputy shares, so the two sides of a gate can never be shown different
    # rows.
    return drilldown.build_payload(item, dev_root / "work-items" / item_id, dev_root, ctx.cwd,
                                   all_items=all_items, events=events, git_health=git_health,
                                   review_mode=review_mode, inbox_origin=inbox_origin,
                                   **drilldown.gate_counters(spine, context_id, item, dev_root,
                                                             git_ops.repo_anchor(ctx, spine)))


@router.post("/dev/work-items/{item_id}/abandon", response_model=AbandonResponse)
async def dev_work_item_abandon(item_id: str, body: AbandonBody,
                                dev: DevKnowledgeService = Depends(get_dev),
                                dev_store: DevStore = Depends(get_dev_store),
                                sessions: SessionStore = Depends(get_sessions),
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Abandon a work-item — HUMAN-ONLY, legal from any non-terminal phase.

    Ordered and each step idempotent: end live runs, remove the worktree, write the abandon note, set
    the terminal status, resume a paused parent."""
    ctx, dev_root, item = _load(body.context_id, item_id, dev)
    if item.get("done_at") or str(item.get("status")) == "done":
        raise HTTPException(status_code=409, detail="item is already terminal")
    # A live run's task cannot be killed from here, and releasing its row would leave a straggler
    # writing.
    if spine.is_item_running(body.context_id, item_id):
        raise HTTPException(status_code=409,
                            detail="a run is in progress for this item — wait for it to finish")
    outcome = "superseded" if body.superseded_by else "abandoned"
    # No dangling supersedes, enforced, not just claimed: the pointer must name a real item.
    if body.superseded_by and not dev.read_work_item(dev_root, str(body.superseded_by)):
        raise HTTPException(status_code=400,
                            detail=f"superseded_by names no existing work-item: {body.superseded_by}")
    all_items = dev.read_all(dev_root)["work_items"]

    # End live work: free run rows, retire the session. No capture sweep — an abandon writes
    # nothing.
    from ...services.runs import stop_item_work
    runs_freed, _ = stop_item_work(body.context_id, item_id)
    for sid in dev.work_item_session_ids(item):   # ALL role threads (intake/build/vet + legacy)
        sessions.delete(ctx, sid, cause="retired")
    # 2. worktree dir removed, branch KEPT. Never blocks the abandon.
    worktree_removed = None
    if item.get("git_worktree"):
        try:
            worktree_removed = bool(git_layer.remove_worktree(ctx.cwd, ctx.id, item_id)["verified"])
        except (git_layer.GitError, git_layer.GitBusy) as e:
            worktree_removed = False
            log.warning("abandon: worktree cleanup failed for %s: %s", item_id, e)
    # An abandon IS how this item closed, so it is recorded in the close report. Code writes the
    # whole file.
    item_dir = dev_root / "work-items" / item_id
    report = item_dir / "reports" / "report-close.md"
    note = (f"\n## Abandon note\n{outcome}"
            + (f" by `{body.superseded_by}`" if body.superseded_by else "")
            + (f" — {body.reason.strip()}" if body.reason.strip() else " (no reason given)")
            + "\n")
    head = "" if report.is_file() else (
        f"# Close — {item.get('title') or item_id}\n\n"
        f"**What landed:** nothing — the item was {outcome} at the `{item.get('phase')}` phase.\n")
    if "## Abandon note" not in (report.read_text() if report.is_file() else ""):
        _atomic_write(report, (head + (report.read_text() if report.is_file() else "")).rstrip()
                      + "\n" + note)
    # 4. terminal — a status change, never a delete (folder, artifacts, branch, trace remain).
    try:
        dev.set_work_item_terminal(dev_root, item_id, outcome,
                                   superseded_by=body.superseded_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # The owner's own ending is its own read receipt; a completed close still pages, because that
    # is genuinely news.
    dev.set_work_item_seen(dev_root, item_id)
    # 5. a paused parent whose last open BLOCKING child this was resumes (typed-awaiting router).
    for it in all_items:
        if it.get("id") == item_id:
            it["status"] = "done"
    resume_id = status_router.parent_to_resume(all_items, item)
    if resume_id:
        dev.set_work_item_status(dev_root, resume_id, "active")
        rel = status_router.relation_of(item)
        dev_store.log_event(body.context_id, "item.resume",
                            f"{rel.capitalize()} child {item_id} abandoned — parent resumed",
                            item_id=resume_id, actor="daemon",
                            meta={"child": item_id, "relation": rel})
    # An abandon is NOT a release: peers page the owner instead, since what they queued behind is
    # never landing.
    for it in all_items:
        if it.get("id") == item_id:
            it["outcome"] = outcome
    scheduler.release_downstream(dev, dev_root, dev_store, body.context_id, all_items, item_id,
                                 cause=outcome)
    # Abandoning a build⟷vet item frees an autopilot slot — pump the queue.
    from ...services import gates as gate_svc
    gate_svc.pump_autopilot_slots(body.context_id)
    # 6. the children triage list — a triage moment, nothing automatic.
    blocking, parallel = [], []
    for it in all_items:
        sf = it.get("spawned_from")
        if not (isinstance(sf, dict) and str(sf.get("item")) == item_id) \
                or status_router.is_terminal(it):
            continue
        (blocking if sf.get("relation") == "blocking" else parallel).append(str(it["id"]))
    dev_store.log_event(body.context_id, "item.abandon",
                        f"{outcome.capitalize()}: {item.get('title') or item_id}"
                        + (f" — {body.reason.strip()}" if body.reason.strip() else ""),
                        item_id=item_id, actor="owner",
                        meta={"outcome": outcome, "superseded_by": body.superseded_by,
                              "blocking_children": blocking})
    log.info("abandoned work-item %s (%s; blocking children: %s)", item_id, outcome, blocking)
    return {"ok": True, "id": item_id, "outcome": outcome,
            "worktree_removed": worktree_removed, "session_cleared": bool(dev.work_item_session_ids(item)),
            "runs_freed": runs_freed, "blocking_children": blocking,
            "parallel_children": parallel}
