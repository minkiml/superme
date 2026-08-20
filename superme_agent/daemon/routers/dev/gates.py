"""Drilldown + lifecycle routes (renovation v2 §4): the work-item surface's server-computed payload,
its per-phase report reads, and the human-only abandon path.

This layer only GATHERS inputs (item, events, git health) — the payload is assembled in
`services/drilldown` over `core/gate_briefs.gate_state`, so the drilldown and the deputy's prompt
read one computation of a gate's checks. Abandon is D8's human-only terminal path: ordered,
idempotent effects; zero general-knowledge writes (write-at-merge means a pre-merge abandon never
wrote anything); blocking children surfaced for the owner's disposal.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...app_state import (
    DevKnowledgeService, DevStore, SessionStore, SystemSpine,
    get_dev, get_dev_store, get_sessions, get_spine,
)
from ....core import artifacts, git_layer, kind_profiles, status_router
from ....core.artifacts import _atomic_write
from ...services import drilldown, git_ops, scheduler
from ....gateway import contexts
from ...schemas.dev.gates import (
    AbandonResponse, DrilldownResponse, OwnerInputResponse, PhaseReportResponse,
)

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
    """One phase's user-facing report (`reports/report-<phase>.md`) for the Reports tab — the markdown
    1:1, plus the path to the full agent-facing contract behind it (§4.3). 404 when that phase hasn't
    written one; the tab greys itself from `DrilldownResponse.reports` rather than probing."""
    _ctx, dev_root, _item = _load(context_id, item_id, dev)
    report = artifacts.report_text(dev_root / "work-items" / item_id, phase)
    if report is None:
        raise HTTPException(status_code=404, detail=f"no report-{phase}.md for this item")
    return report


class OwnerReferenceBody(BaseModel):
    source: str = ""
    description: str = ""


class OwnerNoteBody(BaseModel):
    description: str = ""


class OwnerInputBody(BaseModel):
    """The owner's § From you, whole. Add and delete are both a PUT of the full slot lists — the
    owner is the section's only writer, so there is no concurrent edit for a delta to protect."""
    context_id: str = "global"
    references: list[OwnerReferenceBody] = []
    notes: list[OwnerNoteBody] = []


@router.get("/dev/work-items/{item_id}/from-you", response_model=OwnerInputResponse)
async def dev_work_item_owner_input(item_id: str, context_id: str = "global",
                                    dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """`reports/report-triage.md` § From you — what the owner has written into the one section of
    the item that is theirs. Never 404s on a missing brief: `exists: false` is the editor's cue to
    say triage hasn't written one yet, which is a different thing from a broken read."""
    _ctx, dev_root, _item = _load(context_id, item_id, dev)
    return artifacts.owner_input(dev_root / "work-items" / item_id)


@router.put("/dev/work-items/{item_id}/from-you", response_model=OwnerInputResponse)
async def dev_work_item_set_owner_input(item_id: str, body: OwnerInputBody,
                                        dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Save the owner's own section, replacing it whole and leaving the rest of the brief untouched.

    HUMAN-ONLY, like abandon: there is no agent tool behind it, because the value of the section is
    precisely that an agent did not write it. Returns what is now on disk — the editor shows what
    the plan phase will read, not what was typed."""
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
    """Everything the drilldown renders, computed once (§4): the live strip · the
    WHAT-YOU-NEED-TO-DO card · at-a-glance · the gate's named check rows with `blocking` · every
    control's activation + reason · the Proof rows · which phases have a report.

    Server-computed activation is the point. The gate brief carried `approve_blocked_by` and no
    component ever read it, so the greying rule lived in TypeScript beside the rule the backend
    enforces."""
    ctx, dev_root, item = _load(context_id, item_id, dev)
    # The board's live-run telemetry (running · tokens · ctx) — the Now strip reads `running`, and
    # without the enrich a working agent renders as idle.
    live_by_item = {r["item_id"]: r for r in spine.live_runs(context_id) if r.get("item_id")}
    dev.enrich_work_items(dev_root, [item], live_by_item, spine.run_stats(context_id, mode="dev"))
    events = dev_store.list_events(context_id, item_id=item_id, limit=100)
    # The landing rule is a REPO fact, read unconditionally and passed on its own. It used to be
    # decorated onto `git_health`, which made it disappear whenever there was no branch yet or the
    # health read raised — and `mode != "strict"` then told the owner of a STRICT repo that their
    # repo was `fast`. What "Approve" does at review depends on this, so it must not ride a
    # nullable carrier that answers a different question (dogfood D1, then this).
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
    # WHO raised this — the inbox row's own `origin`, read here because the drilldown service takes
    # no store. A row the agent filed via `create_inbox_item` carries `agent`; an owner capture
    # carries `user`. Absent row (deleted, or an item that never came from the inbox) reads owner.
    inbox_origin = ""
    if item.get("inbox_id"):
        row = dev_store.get_inbox(int(item["inbox_id"])) or {}
        inbox_origin = "agent" if "agent" in (row.get("origin") or []) else "user"
    # Every check the item folder cannot answer — fan-out, the family method, the briefs, surviving
    # probes, the recorded standards — read in ONE place that the deputy shares, so the two sides of
    # a gate can never be shown different rows (see `gate_counters`).
    return drilldown.build_payload(item, dev_root / "work-items" / item_id, dev_root, ctx.cwd,
                                   all_items=all_items, events=events, git_health=git_health,
                                   review_mode=review_mode, inbox_origin=inbox_origin,
                                   **drilldown.gate_counters(spine, context_id, item, dev_root,
                                                             git_ops.repo_anchor(ctx, spine)))


class AbandonBody(BaseModel):
    context_id: str = "global"
    reason: str = ""
    superseded_by: str | None = None  # set → outcome `superseded` (D3: no dangling supersedes)


@router.post("/dev/work-items/{item_id}/abandon", response_model=AbandonResponse)
async def dev_work_item_abandon(item_id: str, body: AbandonBody,
                                dev: DevKnowledgeService = Depends(get_dev),
                                dev_store: DevStore = Depends(get_dev_store),
                                sessions: SessionStore = Depends(get_sessions),
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Abandon a work-item — HUMAN-ONLY (no agent-tool counterpart), legal from any non-terminal
    phase (D8). Ordered, each step idempotent: end live runs/sessions · remove the worktree
    (branch kept — near-free trace) · abandon note into reports/report-close.md · terminal status change
    (`abandoned`, or `superseded` when `superseded_by` names the replacement) · resume a paused
    parent whose last open blocking child this was. Dev-knowledge untouched — write-at-merge
    means a pre-merge abandon wrote nothing, ever. The response is the abandon brief: blocking
    children listed for YOUR disposal (they existed only for this parent); parallel children
    continue untouched."""
    ctx, dev_root, item = _load(body.context_id, item_id, dev)
    if item.get("done_at") or str(item.get("status")) == "done":
        raise HTTPException(status_code=409, detail="item is already terminal")
    # A live run's asyncio task can't be killed from here — releasing its row while it keeps
    # executing would leave a straggler writing into a terminal item. Wait it out first.
    if spine.is_item_running(body.context_id, item_id):
        raise HTTPException(status_code=409,
                            detail="a run is in progress for this item — wait for it to finish")
    outcome = "superseded" if body.superseded_by else "abandoned"
    # D3 "no dangling supersedes" — enforced, not just claimed: the pointer must name a real item.
    if body.superseded_by and not dev.read_work_item(dev_root, str(body.superseded_by)):
        raise HTTPException(status_code=400,
                            detail=f"superseded_by names no existing work-item: {body.superseded_by}")
    all_items = dev.read_all(dev_root)["work_items"]

    # 1. end live work: free run rows (history kept), retire the session (transcript reclaimed,
    #    trace preserved + labeled). No capture sweep — an abandon writes nothing anywhere.
    from ...services.runs import stop_item_work
    runs_freed, _ = stop_item_work(body.context_id, item_id)
    for sid in dev.work_item_session_ids(item):   # ALL role threads (intake/build/vet + legacy)
        sessions.delete(ctx, sid, cause="retired")
    # 2. worktree dir removed, branch KEPT (D4 terminal cleanup). Never blocks the abandon.
    worktree_removed = None
    if item.get("git_worktree"):
        try:
            worktree_removed = bool(git_layer.remove_worktree(ctx.cwd, ctx.id, item_id)["verified"])
        except (git_layer.GitError, git_layer.GitBusy) as e:
            worktree_removed = False
            log.warning("abandon: worktree cleanup failed for %s: %s", item_id, e)
    # 3. abandon note into reports/report-close.md — why, in the owner's words. An abandon IS how
    #    this item closed, and every user-facing doc is `report-<phase>.md` (renovation §3.3), so
    #    the ending is recorded in the close report. No agent runs here, so CODE writes the whole
    #    file: the facts are the item record's, not a claim anyone has to author.
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
    # 4b. THE OWNER'S OWN ENDING IS ITS OWN READ RECEIPT. `unread` (S7) exists to push a closeout
    #     the owner has not seen; abandon is human-only, so the notice would be addressed to its
    #     author. Without this the strip filled with items the owner had just dropped and could
    #     only clear by re-opening each one (owner, 2026-08-09). A COMPLETED close still pages —
    #     that one is genuinely news, and an autopilot item can land overnight unwatched.
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
    # 5b. peers parked on this item (`after:`) — an abandon/supersede is NOT a release. They page
    #     the owner instead: the thing they were queued behind is never landing.
    for it in all_items:
        if it.get("id") == item_id:
            it["outcome"] = outcome
    scheduler.release_downstream(dev, dev_root, dev_store, body.context_id, all_items, item_id,
                                 cause=outcome)
    # Abandoning a build⟷vet item frees an autopilot slot — pump the queue.
    from ...services import gates as gate_svc
    gate_svc.pump_autopilot_slots(body.context_id)
    # 6. the children triage list (D8: a triage moment, nothing automatic).
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
