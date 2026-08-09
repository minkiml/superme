"""Work-item lifecycle routes: the plan/design → build/eval → done pipeline + its background "Plan it"
quick-action, review payload, call-trail, model config, and the phase-advance gate.

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
    """The owner's manual RUN — fire the current phase's own background run (owner, 2026-07-31).

    ONE route replacing `/plan` and `/vet`, which were two doors onto the same dispatcher with
    hand-written per-phase guards that drifted from it (`/plan` refused anything outside `plan`,
    `/vet` outside `vet`, and neither could reach triage/build/investigate at all). This is the
    manual driver for a repo that is not on autopilot; on autopilot every phase fires itself.

    409 when the item is terminal, stopped (that is Resume's), parked at a gate (that is Approve's),
    at a phase with no run of its own, or already running — the same rule the drilldown's `run`
    button reads, so a live-looking button can never 409."""
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
    """The owner's RESUME on a work-item whose run STOPPED (recovery R4): re-fire the phase's own
    background run. Nothing is rewound — the branch, the worktree and every artifact stand; only the
    run is new, which is what makes this cheap enough to offer as a button.

    Distinct from Re-run, which reads alike and does the opposite: Resume picks the run back up
    that hit a wall it could not pass and carries the gap to review (the RUN succeeded, the work
    stopped). Resume re-runs a run that never finished (the work is fine, the RUN stopped).

    409 when the item isn't stopped, is terminal, has a run in flight, or sits at a phase with no
    background run to re-fire — the same conditions the drilldown's button reads to stay inactive,
    from the same function, so the tooltip can never disagree with the outcome."""
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
    """The owner's RE-RUN: throw this item's work away and start it over in place (recovery R5).
    Destructive, and the only recovery act that is — artifacts, reports, checkpoints, the deputy's
    log and every session are DELETED, the worktree dir is removed, and the item re-enters at its
    kind's first phase with `generation` bumped.

    What it keeps is what makes it a re-run and not a new item: the id, the branch, every run and
    dev-activity row (permanent trace), the inbox row, `preliminary/`, and every graph relation.

    Distinct from `/resume`, which rewinds NOTHING and re-fires the run that died. Reach for this
    only when there is no run worth re-firing. 409 when the item is terminal or has a run in
    flight — the same rule the drilldown's button reads, from the same function."""
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
    """The owner's grant/deny on a deferred authorization at review. Both RECORD and route nothing
    (renovation §2.1): the item stays at review so every pending request can be resolved in any
    order, and one exit then fires — Approve (close applies the granted ops, skips the denied ones
    and records the gap) or `revise` (they land as plan input). `denied` also waives the blocked
    check. Unlike the deputy, the owner grants unconditionally — the delegated-authority floor
    binds only the deputy. 409 when the request isn't in a decidable state."""
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


# Disposal is ONE act — abandon (`POST .../abandon`), the drilldown's Drop. The hard-delete route
# that stood here erased the item folder, its session transcripts and the originating inbox row.
# It went (owner, 2026-08-02) because it could only ever run pre-build, so it could not be THE
# disposal act; because no terminal event fires for a deleted item, so every wedge it caused (a
# parent stuck at awaiting_child, a downstream parked on a vanished id, a live run never released)
# had to be hand-simulated here; and because erasing the inbox row contradicts inbox_flow's own
# contract that the pushed row survives as trace. `dev.delete_work_item` itself STAYS — the
# Prompt X-ray probe uses it to tear down its own throwaway item.


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
        # The execution SNAPSHOT (present once the item is completed; live items use the rows).
        "execution": dev.read_artifact_text(dev_root, item_id, "execution.md"),
        "artifact_status": artifacts.artifact_status(item, item_dir, ctx.cwd),
        # S7 drilldown: the remaining gate docs as raw text (rendered per-phase sub-tab). The
        # retired names (validation/readiness/closeout) are GONE — asking artifact_file for one
        # now raises, so this list tracks `_SPECS`, never a remembered set.
        "docs": {name: dev.read_artifact_text(dev_root, item_id, artifacts.artifact_file(name))
                 for name in ("brief", "investigation")},
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
    run (newest run first, calls in order within a run) — the drilldown's Runs pane.

    `runs` rides along so each group can say WHAT that run was. Without it a group header reads
    "Run #653" and the owner has to guess why it opened with a shell command instead of a phase
    skill — the answer being that it was a chat turn, or a resumed build cycle, or the conflict
    resolver. Naming the feature answers it on the row."""
    return {"artifacts": spine.events_for_item(context_id, item_id),
            "runs": spine.runs_for_item(context_id, item_id)}


@router.get("/dev/work-items/{item_id}/timeline", response_model=WorkItemTimelineResponse)
async def dev_work_item_timeline(item_id: str, context_id: str = "global") -> dict:
    """F2 unified timeline: every run of this item, oldest-first, phase/role-tagged with its ordered
    turn events (prompt · reply · calls) — the read-only history the chat panel loads before
    live-streaming new frames from the item's event broker. All phases in one chronological view."""
    return build_item_timeline(context_id, item_id)


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
    return HTMLResponse(render_input_page(data))


@router.get("/dev/work-items/{item_id}/doc.html", response_class=HTMLResponse)
async def dev_work_item_doc(item_id: str, path: str, context_id: str = "global",
                            dev: DevKnowledgeService = Depends(get_dev)) -> HTMLResponse:
    """One of the item's AGENT-FACING artifacts (brief.md · plan.md · build-vet-<n>.md) as a
    standalone page — what a report's "full contract" link opens. `path` is the report's own
    relative pointer; anything outside the item's `artifacts/` folder is refused as missing."""
    from ...services.doc_preview import (editable_artifact, render_doc_page,
                                         render_missing_doc_page, resolve_doc)
    item_dir = _dev_root(context_id) / "work-items" / item_id
    target = resolve_doc(item_dir, path)
    if target is None:
        return HTMLResponse(render_missing_doc_page(item_id, path), status_code=404)
    # The edit bar appears only for the two INTENT artifacts, and only while the item can still act
    # on a change. A terminal item's plan is a record of what was done — offering a button the PUT
    # route would then refuse is worse than not offering it.
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
    """The owner hand-edits `brief.md` or `plan.md` — the item's two statements of INTENT, and the
    only artifacts they may write (`artifacts.OWNER_EDITABLE`). Everything else under `artifacts/`
    is a record of what a run did; the edit mode never offers it and this route refuses it.

    Refused, each for its own reason: a LIVE run (a phase agent may be writing the same file — the
    last writer would silently win) · a TERMINAL item (its work is merged and closed; editing the
    plan now rewrites history rather than steering anything) · text that fails the artifact's own
    self-check (returned as `issues` with nothing written, so a save can never leave the item in a
    state the gate refuses and the owner can't see).

    A successful save stamps `edited_by_owner` into the frontmatter. That stamp is the point: an
    agent re-reading this plan next cycle is reading the OWNER's words, not its own."""
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
    """Prompt X-ray: fire a THROWAWAY prompt-extraction probe on this repo — a disposable work-item
    that runs the real lifecycle unattended to capture each phase's actual input prompt, then tears
    itself down (folder + worktree + branch, keeping only the tagged run trace). One at a time per
    repo. Returns the current probe state (running + captured links)."""
    from ...services import prompt_extraction as px
    return px.launch(context_id)


@router.get("/dev/prompt-extraction/status", response_model=PromptExtractionStatusResponse)
async def dev_prompt_extraction_status(context_id: str = "global") -> dict:
    """Prompt X-ray: the repo's current probe state — whether one is running, and the captured "A"
    input-page links for the last probe (which survive its teardown)."""
    from ...services import prompt_extraction as px
    return px.status(context_id)


# NOTE: `POST /dev/work-items/{id}/archive` was RETIRED (2026-07-31). It folded a terminal item's
# files into `archive.zip` — but nothing that READS artifacts knew about the zip, so archiving a
# shipped item blanked its Reports tab and made `artifact_status` report its plan as `missing`,
# rendering a red "BLOCKS APPROVE" on work that had already merged. The only thing it bought was
# disk: ~110 KB per item, in a gitignored knowledge home. Deleting it removed the defect with it.
# It did NOT clear the item from the board — `done_at` does that (see panels.tsx `isActive`).
# If folder size ever matters, it returns as an automatic background compaction WITH read-through,
# never as a button: storage is not something the owner should have to think about.
#
# Completion itself has no route either: an item goes terminal MECHANICALLY when its closing run
# reports (services/clearance) — there is no owner promotion and no agent proposal.


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
    (an awaiting_human item just got its answer). The autopilot driver uses the same core."""
    ctx = contexts.resolve(context_id, "dev")
    return gates.advance_item(ctx, context_id, item_id, dev=dev, dev_store=dev_store,
                              spine=spine, actor="owner")


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
