"""Resume a stopped work-item — re-fire the run that died.

Nothing is rewound: the branch, worktree and artifacts stand, only the run is new. One function,
not five call sites, so auto-resume and the owner's button cannot drift.
"""

import asyncio
import logging

from ..app_state import dev as _dev, dev_store as _dev_store, spine as _spine
from ...core import deputy as _deputy
from ...gateway import contexts
from . import run_tasks

log = logging.getLogger("superme-agent")

# Every phase that owns a background run. A phase absent here has no run to re-fire.
RESUMABLE_PHASES = ("triage", "plan", "build", "vet", "investigate", "review", "close")


def resume_reason(item: dict, *, running: bool) -> tuple[bool, str]:
    """Can this item be resumed, and the one line saying why or why not. PURE.

    The drilldown and the route share it, so a tooltip cannot disagree with the route."""
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
    """Clear a stopped item's error and re-fire its phase's run.

    The status is cleared FIRST because every firer refuses a non-active item, and restored to `error`
    if nothing starts."""
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
        # An unconsumed send-back is re-delivered, not dropped: the ordinary prompt says nothing
        # about what was asked for.
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
        # Clearing the error makes the item eligible again and drops `error_reason` with it.
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
    """The owner's manual RUN: fire the current phase's own background run.

    The dispatcher Resume uses, with a different precondition."""
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
    run-lock in `begin_run`), so this adds no locking of its own."""
    from .runs import (begin_run, run_background_item_skill, run_background_plan,
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
    # The two intake skills with no dedicated firer, dispatched the way an approve dispatches
    # them.
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    model = _spine.effective_model(context_id, item_model=item.get("model"))
    effort = _spine.effective_effort(context_id, item_effort=item.get("effort"))
    if begin_run(ctx, context_id, item_id, phase, model, phase=phase) is None:
        return False, "a run is already in flight"
    item_dir = dev_root / "work-items" / item_id
    coro = (run_background_plan(ctx, context_id, item_id, item_dir, model, effort)
            if phase == "plan" else
            run_background_item_skill(ctx, context_id, item_id, item_dir, phase, model, effort))
    run_tasks.track(asyncio.create_task(coro))
    return True, f"re-fired the {phase} run"
