"""Resume a stopped work-item — re-fire the run that died (recovery-resilience R4).

`error` (R2) says the work stopped. This is how it starts again: clear the status and re-fire the
phase's own background run, the SAME firer the workflow would have used. Nothing about the item's
work is rewound — the branch, the worktree, the artifacts, the transcripts all stand; only the run
is new. That is what makes Resume cheap and safe to offer, and it is why it is a different act from
re-run (R5), which deliberately throws work away.

**Resume is not Continue.** They read alike and do opposite things, which is exactly why they keep
separate names (owner, 2026-07-31):

    Continue  a build parked at a wall it cannot pass → finalize what's doable and carry the gap
              forward to review. The run SUCCEEDED; the work is what stopped.
    Resume    a run that never finished → run it again. The work is fine; the RUN is what stopped.

**Why this is one function and not five call sites.** R3 will auto-fire exactly this on a healthy
restart, and the owner's button fires it now. If auto-resume grew its own dispatch table the two
would drift, and the drift would be invisible until an outage — the worst possible time to discover
that the automatic path resumes four phases and the manual one resumes six.

Best-effort and honest about failure: if no run starts, the item goes straight back to `error` with
the reason, because an item left `active` with nothing running is the silent wedge this whole
project exists to remove.
"""

import asyncio
import logging

from ..app_state import dev as _dev, dev_store as _dev_store, spine as _spine
from ...core import deputy as _deputy
from ...gateway import contexts
from . import run_tasks

log = logging.getLogger("superme-agent")

# The phases a stopped item can be resumed at — every phase that owns a background run. `triage`,
# `plan`, `investigate`, `review` and `close` each have a firer; `build` and `vet` are the loop's.
# A phase absent here has no run to re-fire, so Resume would be a button that does nothing.
RESUMABLE_PHASES = ("triage", "plan", "build", "vet", "investigate", "review", "close")


def resume_reason(item: dict, *, running: bool) -> tuple[bool, str]:
    """Can this item be resumed, and the one line saying why or why not. PURE — the drilldown's
    action list and the route share it, so the button's tooltip can never disagree with what the
    route actually does."""
    if item.get("done_at") or str(item.get("status")) == "done":
        return False, "this item is terminal — nothing to resume"
    if str(item.get("status")) != "error":
        return False, ("nothing has stopped — Resume appears when a run dies (an outage, a crash, "
                       "a restart mid-run)")
    if running:
        return False, "a run is already in flight"
    phase = str(item.get("phase") or "")
    if phase not in RESUMABLE_PHASES:
        return False, f"`{phase or 'this phase'}` has no background run to re-fire"
    return True, (f"re-fires the {phase} run that stopped. Nothing is rewound — the branch, the "
                  f"worktree and every artifact stand; only the run is new")


def resume_item(context_id: str, item_id: str) -> tuple[bool, str]:
    """Clear a stopped item's error and re-fire its phase's run. Returns (started, reason).

    The status is cleared FIRST because every firer refuses a non-active item (the owner-hold rule
    they all share) — and restored to `error` if nothing starts, so a failed Resume leaves the item
    exactly as it found it rather than `active` with no run."""
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False, "context has no internal root"
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id)
        if item is None:
            return False, "work-item not found"
        can, why = resume_reason(item, running=_spine.is_item_running(context_id, item_id))
        if not can:
            return False, why
        phase = str(item.get("phase"))
        was = str(item.get("error_reason") or "")
        # An UNCONSUMED send-back is re-delivered, not dropped. `_fire` re-runs the phase with its
        # ordinary prompt, which says nothing about what the deputy asked for — so a resumed item
        # whose last verdict was "go fix this" reads its own finished transcript and no-ops, while
        # the deputy keeps paying a full pass to re-derive the same verdict. Routed through the
        # feedback firer instead, which is the path that carries the reason and the asked-for change.
        pending = _deputy.pending_send_back(dev_root / "work-items" / item_id)
        if pending:
            from .runs import fire_phase_feedback
            _dev.set_work_item_status(dev_root, item_id, "active")
            started = fire_phase_feedback(
                context_id, item_id, phase=phase,
                feedback=str(pending.get("change") or pending.get("because") or ""), by="deputy")
            if started:
                _dev_store.log_event(
                    context_id, "run.resume",
                    f"Resumed the {phase} run, carrying the deputy's send-back",
                    item_id=item_id, actor="owner",
                    meta={"phase": phase, "was": was[:200],
                          "send_back": str(pending.get("because") or "")[:300]})
                log.info("resumed %s at %s with the pending send-back", item_id, phase)
                return True, f"re-fired the {phase} run with the deputy's send-back"
            _dev.set_work_item_error(dev_root, item_id, was or "the work stopped")
            log.warning("resume: feedback re-fire failed for %s — falling back to a plain re-run",
                        item_id)
        # Clearing the error is what makes the item eligible again — and `set_work_item_status`
        # drops `error_reason` with it, so a resumed item never carries a stale explanation.
        _dev.set_work_item_status(dev_root, item_id, "active")
        started, detail = _fire(ctx, context_id, item_id, phase)
        if not started:
            _dev.set_work_item_error(dev_root, item_id, was or "the work stopped")
            return False, detail
        _dev_store.log_event(
            context_id, "run.resume", f"Resumed the {phase} run that stopped",
            item_id=item_id, actor="owner", meta={"phase": phase, "was": was[:200]})
        log.info("resumed %s at %s", item_id, phase)
        return True, detail
    except Exception:
        log.exception("resume failed for %s", item_id)
        return False, "resume failed — see the daemon log"


def run_phase(context_id: str, item_id: str) -> tuple[bool, str]:
    """The owner's manual RUN: fire the current phase's own background run (owner, 2026-07-31).

    The driver for a repo that is NOT on autopilot — the same dispatcher Resume uses, with a
    different precondition. Resume answers "the run died, start it again"; this answers "nothing has
    run yet, start it". Refuses a terminal item, an item at a gate (that slot is Approve's), a
    stopped item (that slot is Resume's) and a run already in flight. Returns (started, reason).
    """
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False, "context has no internal root"
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id)
        if item is None:
            return False, "work-item not found"
        if item.get("done_at") or str(item.get("status")) == "done":
            return False, "this item is terminal"
        if str(item.get("status")) == "error":
            return False, "the run stopped — use Resume, which re-fires it and clears the error"
        if str(item.get("status")) == "awaiting_human":
            return False, "this item is waiting on your decision, not on a run"
        if _spine.is_item_running(context_id, item_id):
            return False, "a run is already in progress for this item"
        phase = str(item.get("phase") or "")
        if phase not in RESUMABLE_PHASES:
            return False, f"`{phase or 'this phase'}` has no background run to fire"
        started, why = _fire(ctx, context_id, item_id, phase)
        if started:
            _dev_store.log_event(context_id, "item.run",
                                 f"Owner fired the {phase} run",
                                 item_id=item_id, actor="owner", meta={"phase": phase})
        return started, why
    except Exception:
        log.exception("run_phase failed for %s", item_id)
        return False, "the run could not be started — see the daemon log"


def _fire(ctx, context_id: str, item_id: str, phase: str) -> tuple[bool, str]:
    """Dispatch to the phase's own firer. Each one already refuses a double-fire (the per-item
    run-lock in `_begin_run`), so this adds no locking of its own."""
    from .runs import (_begin_run, _run_background_item_skill, _run_background_plan,
                       fire_auto_triage, fire_close_run, fire_review_entry)
    if phase == "build":
        from .loop import start_build_cycle
        return start_build_cycle(ctx, context_id, item_id)
    if phase == "vet":
        from .loop import start_vet_run
        return start_vet_run(ctx, context_id, item_id)
    if phase == "triage":
        return (fire_auto_triage(context_id, item_id, _spine),
                "re-fired the triage run")
    if phase == "review":
        return fire_review_entry(context_id, item_id, _spine), "re-fired the review-entry run"
    if phase == "close":
        return fire_close_run(context_id, item_id, _spine), "re-fired the closing run"
    # plan / investigate — the two intake skills with no dedicated firer, dispatched the same way
    # `gates.advance_item` dispatches them on an approve.
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    model = _spine.effective_model(context_id, item_model=item.get("model"))
    effort = _spine.effective_effort(context_id, item_effort=item.get("effort"))
    if _begin_run(ctx, context_id, item_id, phase, model, phase=phase) is None:
        return False, "a run is already in flight"
    item_dir = dev_root / "work-items" / item_id
    coro = (_run_background_plan(ctx, context_id, item_id, item_dir, model, effort)
            if phase == "plan" else
            _run_background_item_skill(ctx, context_id, item_id, item_dir, phase, model, effort))
    run_tasks.track(asyncio.create_task(coro))
    return True, f"re-fired the {phase} run"
