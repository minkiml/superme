"""Mechanical clearance — the post-CLOSE kernel hook.

Clearance is what makes a work-item Done: no agent proposes it and no owner clicks it. It ALWAYS
COMPLETES, and refuses exactly one thing — a non-terminal blocking child, which is genuine
waiting.
"""

import logging

from ..app_state import dev as _dev, dev_store as _dev_store, \
    spine as _spine, sessions as _sessions
from ...core import autopilot as _autopilot
from ...core import gate_briefs, git_layer
from ...core.vocab import kind_profiles, status_router
from ...gateway import contexts

log = logging.getLogger("superme-agent")

# Two retries of a closing run that reported nothing, then the gap is recorded and the item moves
# on.
MAX_CLOSE_RETRY = 2

_MAX_PARENT_DEPTH = 4   # a cleared child releases its parent, which may itself be clearable


def _refused(reason: str) -> dict:
    return {"ok": False, "refused": reason}


def clear_item(context_id: str, item_id: str, *, actor: str = "daemon",
               knowledge_gap: str | None = None, _depth: int = 0) -> dict:
    """Clear a close-phase work-item to terminal.

    Returns `{"ok": False, "refused": …}` when the item genuinely cannot clear yet. Never raises, never
    deletes a log row. `knowledge_gap` records that the closing run never landed its writes; the item
    clears regardless."""
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        return _refused("context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id)
    if item is None:
        return _refused("work-item not found")
    if item.get("done_at") or str(item.get("status")) == "done":
        return {"ok": True, "id": item_id, "already": True}
    if not kind_profiles.is_final_phase(item.get("kind"), item.get("phase") or "triage"):
        return _refused("only close-phase items can be cleared")
    if _spine.is_item_running(context_id, item_id):
        return _refused("a run is in progress for this item")
    # The kind's declared criteria, evaluated mechanically. Review's exit locked code and git, so
    # the WORK cannot refuse here.
    all_items = _dev.read_all(dev_root)["work_items"]
    cr = gate_briefs.close_readiness(item, dev_root / "work-items" / item_id, all_items)
    if not cr["ok"]:
        failed = [c for c in cr["checks"] if not c["ok"]]
        fails = "; ".join(f"{c['criterion']}: {c['detail']}" for c in failed)
        # The status must name what will release it: only a children failure keeps
        # `awaiting_child`, which has a mechanism behind it.
        waits_on_child = any(str(c["criterion"]) == "children_terminal" for c in failed)
        _dev.set_work_item_status(dev_root, item_id,
                                  "awaiting_child" if waits_on_child else "awaiting_human")
        _dev_store.log_event(context_id, "close.waiting",
                             f"Clearance waiting — {fails}",
                             item_id=item_id, actor="daemon", meta={"refused": fails})
        return _refused(f"close criteria not met — {fails}")
    if knowledge_gap:
        _dev_store.log_event(context_id, "close.knowledge_failed",
                             f"Cleared without the knowledge write — {knowledge_gap}",
                             item_id=item_id, actor="daemon", meta={"gap": knowledge_gap})
    # 1. snapshot BEFORE freeing rows.
    from .runs import render_execution_md   # lazy: runs.py drives clearance
    md = render_execution_md(context_id, item_id, item)
    _dev.write_artifact(dev_root, item_id, "execution.md", md)
    # 2. terminal: status=done + outcome=completed + done_at (status change, never a delete).
    _dev.set_work_item_terminal(dev_root, item_id, "completed")
    # Remove the worktree dir, keep the branch ref. Failure is surfaced but never blocks
    # clearance.
    worktree_removed = None
    if item.get("git_worktree"):
        try:
            res = git_layer.remove_worktree(ctx.cwd, ctx.id, item_id)
            worktree_removed = bool(res["verified"])
        except (git_layer.GitError, git_layer.GitBusy) as e:
            worktree_removed = False
            log.warning("worktree cleanup failed for %s: %s", item_id, e)
    # If this was the last open blocking child, auto-resume the parent. No human involved.
    for it in all_items:
        if it.get("id") == item_id:
            it["status"] = "done"
    resume_id = status_router.parent_to_resume(all_items, item or {"id": item_id})
    if resume_id:
        _dev.set_work_item_status(dev_root, resume_id, "active")
        rel = status_router.relation_of(item or {})
        _dev_store.log_event(context_id, "item.resume",
                             f"{rel.capitalize()} child {item_id} closed — parent resumed",
                             item_id=resume_id, actor="daemon",
                             meta={"child": item_id, "relation": rel})
        parent = _dev.read_work_item(dev_root, resume_id) or {}
        if (_depth < _MAX_PARENT_DEPTH
                and kind_profiles.is_final_phase(parent.get("kind"),
                                                 parent.get("phase") or "triage")):
            clear_item(context_id, resume_id, actor="daemon", _depth=_depth + 1)
    # Release every item parked on this one's `after:` edge. Only a COMPLETED upstream releases.
    for it in all_items:
        if it.get("id") == item_id:
            it["outcome"] = "completed"
    from . import scheduler, gates   # lazy: both reach back into runs
    scheduler.release_downstream(_dev, dev_root, _dev_store, context_id, all_items, item_id,
                                 cause="completed")
    # An item clearing out of the pipeline frees an autopilot slot — pump the queue.
    gates.pump_autopilot_slots(context_id)
    # The transcript purge is chained behind the sweep, which must read it first. With learning
    # off, purge anyway.
    from .learning import _fire_sweep_bg   # lazy
    session_ids = _dev.work_item_session_ids(item)   # ALL role threads (intake/build/vet + legacy)
    for sid in session_ids:
        if _spine.learning_enabled_for(context_id):
            _fire_sweep_bg(ctx, sid, then_delete="retired")
        else:
            _sessions.delete(ctx, sid, cause="retired")  # workflow done → retired
    from .runs import stop_item_work   # lazy: runs.py drives clearance
    runs_freed, _ = stop_item_work(context_id, item_id)
    _dev_store.log_event(context_id, "item.complete",
                         f"Cleared: {item.get('title') or item_id}",
                         item_id=item_id, actor=actor,
                         meta={"runs_freed": runs_freed,
                               **({"knowledge_gap": knowledge_gap} if knowledge_gap else {})})
    # A probe tears itself down HERE, at the terminal moment: clearance is the one point every
    # finished item passes.
    if _autopilot.is_prompt_extraction(item):
        from . import prompt_extraction as px
        px.teardown(context_id, item_id, reason="probe completed")
    out = {"ok": True, "id": item_id, "execution_snapshot": "artifacts/execution.md",
           "session_cleared": bool(session_ids), "runs_freed": runs_freed}
    if worktree_removed is not None:
        out["worktree_removed"] = worktree_removed
    return out


def close_retries(context_id: str, item_id: str) -> int:
    """How many closing runs have already failed for this item. Read from the event trail, not
    from memory — a daemon restart must not reset the retry budget."""
    try:
        rows = _dev_store.list_events(context_id, item_id=item_id, limit=100)
    except Exception:
        return 0
    return sum(1 for r in rows if str(r.get("kind") or "") == "close.retry")
