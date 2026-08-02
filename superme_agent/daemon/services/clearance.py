"""Mechanical clearance — the post-CLOSE kernel hook (workflow-renovation-v2 §2, slice 5d).

Clearance is what makes a work-item **Done**, and it is MECHANICAL: no agent proposes it and no
owner clicks it. The closing run's job is knowledge (anchor-doc ops + the weekly change-log
entry); the moment it reports, the kernel clears the item — execution snapshot, terminal stamp,
worktree removal, parent + downstream release, session and run-row reclamation.

The rule that shapes this module: **clearance always completes.** A closing run that crashes is
retried by the driver; after the retry budget it clears ANYWAY with the knowledge gap on record
(`close.knowledge_failed`). A broken closing run is a SuperMe fault to fix — never a work-item
that sits at close forever.

Clearance still refuses exactly one thing: a non-terminal blocking child (D3). That is not a
fault — the parent is genuinely waiting on work that exists — so it rests and clears itself when
the last child clears (the resume branch below re-enters clearance for the parent).
"""

import logging

from ..app_state import dev as _dev, dev_store as _dev_store, \
    spine as _spine, sessions as _sessions
from ...core import gate_briefs, git_layer, kind_profiles, status_router
from ...gateway import contexts

log = logging.getLogger("superme-agent")

# How many times the kernel re-fires a closing run that produced no completion report before it
# clears the item anyway. Two retries, then the gap is recorded and the item moves on.
MAX_CLOSE_RETRY = 2

_MAX_PARENT_DEPTH = 4   # a cleared child releases its parent, which may itself be clearable


def _refused(reason: str) -> dict:
    return {"ok": False, "refused": reason}


def clear_item(context_id: str, item_id: str, *, actor: str = "daemon",
               knowledge_gap: str | None = None, _depth: int = 0) -> dict:
    """Clear a close-phase work-item to terminal. Returns `{"ok": True, ...}` on success, or
    `{"ok": False, "refused": <why>}` when the item genuinely cannot clear yet (blocking child,
    a run still in flight, not at close). Never raises; never deletes a log row.

    `knowledge_gap` records that the closing run never landed its knowledge writes — the item
    clears regardless (clearance always completes) and the gap goes on the permanent trail.
    """
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
    # Close criteria (S6/D8): the kind's declared criteria evaluated MECHANICALLY. After slice 5a
    # that is children-terminal and (for research) the report + its itemization decision —
    # review's exit locked code and git, so nothing about the WORK can refuse here.
    all_items = _dev.read_all(dev_root)["work_items"]
    cr = gate_briefs.close_readiness(item, dev_root / "work-items" / item_id, all_items)
    if not cr["ok"]:
        fails = "; ".join(f"{c['criterion']}: {c['detail']}" for c in cr["checks"] if not c["ok"])
        _dev.set_work_item_status(dev_root, item_id, "awaiting_child")
        _dev_store.log_event(context_id, "close.waiting",
                             f"Clearance waiting — {fails}",
                             item_id=item_id, actor="daemon", meta={"refused": fails})
        return _refused(f"close criteria not met — {fails}")
    if knowledge_gap:
        _dev_store.log_event(context_id, "close.knowledge_failed",
                             f"Cleared without the knowledge write — {knowledge_gap}",
                             item_id=item_id, actor="daemon", meta={"gap": knowledge_gap})
    # 1. snapshot BEFORE freeing rows.
    from .runs import _render_execution_md   # lazy: runs.py drives clearance
    md = _render_execution_md(context_id, item_id, item)
    _dev.write_artifact(dev_root, item_id, "execution.md", md)
    # 2. terminal: status=done + outcome=completed + done_at (status change, never a delete).
    _dev.set_work_item_terminal(dev_root, item_id, "completed")
    # 2a. S4 terminal git cleanup: remove the worktree DIR, KEEP the branch ref (near-free trace —
    #     never-delete holds; the record on the item stays too). Failure is surfaced, never silent,
    #     and never blocks clearance (the item is done; a stray dir is a reconciliation concern).
    worktree_removed = None
    if item.get("git_worktree"):
        try:
            res = git_layer.remove_worktree(ctx.cwd, ctx.id, item_id)
            worktree_removed = bool(res["verified"])
        except (git_layer.GitError, git_layer.GitBusy) as e:
            worktree_removed = False
            log.warning("worktree cleanup failed for %s: %s", item_id, e)
    # 2b. typed-awaiting router: if this was the last open BLOCKING child of an awaiting_child
    #     parent, auto-resume the parent (no human involved — D2). A parent released at its own
    #     close phase clears straight through — nothing is left holding it.
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
    # 2c. peer scheduler: release every item parked at `awaiting_upstream` on this one (the
    #     `after:` edge). Only a COMPLETED upstream releases — see services/scheduler.py.
    for it in all_items:
        if it.get("id") == item_id:
            it["outcome"] = "completed"
    from . import scheduler, gates   # lazy: both reach back into runs
    scheduler.release_downstream(_dev, dev_root, _dev_store, context_id, all_items, item_id,
                                 cause="completed")
    # An item clearing out of the pipeline frees an autopilot slot — pump the queue.
    gates.pump_autopilot_slots(context_id)
    # 3. reclaim disk: release_item_runs is STATUS-ONLY (rows are permanent — never-delete-logs;
    #    it just closes any live row); the session transcript is reclaimed AFTER a final capture
    #    sweep (WI-8) — the sweep must read the transcript before it's purged, so the purge is
    #    chained behind the background sweep. When auto-learning is OFF we skip the sweep but
    #    STILL purge (disk reclamation is not a learning concern).
    from .learning import _fire_sweep_bg   # lazy
    session_ids = _dev.work_item_session_ids(item)   # ALL role threads (intake/build/vet + legacy)
    for sid in session_ids:
        if _spine.learning_enabled_for(context_id):
            _fire_sweep_bg(ctx, sid, then_delete="retired")
        else:
            _sessions.delete(ctx, sid, cause="retired")  # workflow done → retired
    runs_freed = _spine.release_item_runs(context_id, item_id)
    _dev_store.log_event(context_id, "item.complete",
                         f"Cleared: {item.get('title') or item_id}",
                         item_id=item_id, actor=actor,
                         meta={"runs_freed": runs_freed,
                               **({"knowledge_gap": knowledge_gap} if knowledge_gap else {})})
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
