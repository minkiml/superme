"""Re-run a work-item — throw the work away and start it over (recovery-resilience R5).

The last rung of the recovery ladder. Resume (R4) re-fires a run and rewinds nothing; re-run is
the opposite act, and the only destructive one: the artifacts, the reports, the checkpoints, the
deputy's log, the worktree and every session go, and the item re-enters at its kind's first phase
as if it had just been pushed. It exists so that no item is ever a dead end — when an item is
wedged in a shape Resume cannot fix, this is the way out that does not cost the owner its identity.

**In place, never a new id** (owner, 2026-07-31). A re-run keeps the item's id, its place in the
work-graph, and every edge pointing at it. A fresh duplicate was considered and rejected: children
are FOLDER-NESTED (parent_id/root_id derive from the path), so a duplicated child would silently
become a root item, and every `after:` peer, `spawned_from` edge and inbox `routed_to` pointer
would need re-pointing — each one a chance to strand live work.

**A restart must be a restart, in all three places.** Keeping the id is not the same as keeping
the attempt, and it took two passes to get all three:

    the FILES      artifacts, reports, checkpoints, the deputy log → deleted outright.
    the ROWS       runs, run events, dev events → SOFT-deleted (`discarded_at`). Nothing is
                   removed ([[never-delete-logs]]); the item's own surfaces stop showing them
                   while every accounting read still counts them. This is what makes the drilldown
                   read as a fresh item instead of stacking one attempt on the last.
    the BRANCH     re-cut to the sha it was originally cut from, old tip parked on a backup ref.
                   Without this the new build checks out a branch that still carries the discarded
                   commits and continues from there — a fresh start in the UI only.

**What survives, and why:**

    branch ref          KEPT (moved, not deleted) — and every discarded commit stays reachable
                        through `refs/backup/<item>-<ts>`.
    runs · run events   KEPT and stamped — permanent trace. The `item.rerun` event is written
    · dev activity      AFTER the stamping, so it survives unstamped: the act stays on the item's
                        own trail even though everything it discarded is out of view.
    inbox row           KEPT — the original ask, and `routed_to` still names this same item.
    preliminary/        KEPT — pushed input, not work this item produced.
    relations           KEPT — parent/child, `after` peers, wave/deliverable, cohort.
    sessions            GONE — a re-run that inherited the old thread would inherit the reasoning
                        that produced the work being discarded.

The reset fires the entry-phase run itself, for the same reason Resume does: an item sitting
`active` with nothing running is the silent wedge this whole project exists to remove. The one
exception is an item whose upstreams have not landed — it parks at `awaiting_upstream` and the
scheduler releases it, exactly as a newly-pushed item would.
"""

import logging

from datetime import datetime, timezone

from ..app_state import dev as _dev, dev_store as _dev_store, spine as _spine
from ...core import git_layer
from ...gateway import contexts
from .resume import _fire

log = logging.getLogger("superme-agent")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def rerun_reason(item: dict, *, running: bool) -> tuple[bool, str]:
    """Can this item be re-run, and the one line saying why or why not. PURE — the drilldown's
    action list and the route share it, so a button that looks live can never 409.

    Deliberately NOT conditioned on `error`: the point of re-run is that it is always there. An
    item wedged at a phase Resume refuses, or one whose work the owner simply wants done again,
    both reach for the same control."""
    if item.get("done_at") or str(item.get("status")) == "done":
        return False, ("this item is finished — its branch is landed and its trace is closed. "
                       "Follow-up work is a new item, not a second life for this one")
    if running:
        return False, "a run is in flight — wait for it to finish, or drop the item"
    return True, ("start this item over: its artifacts, reports, checkpoints and sessions are "
                  "cleared, its run trace leaves this item's view, and the branch is re-cut to "
                  "where it started. Nothing is destroyed — the trace still counts toward the "
                  "project's totals and the old commits keep a backup ref")


def rerun_item(context_id: str, item_id: str) -> tuple[bool, str]:
    """Reset a work-item and re-fire its entry phase. Returns (ok, reason).

    Order matters: everything destructive happens BEFORE the fire, so a run can never start
    against a half-torn-down item. Teardown steps are individually best-effort (a missing
    worktree or an already-deleted session must not abort the reset), but the file reset is not —
    if `item.md` cannot be rewritten the whole act reports failure and nothing is fired."""
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False, "context has no internal root"
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id)
        if item is None:
            return False, "work-item not found"
        can, why = rerun_reason(item, running=_spine.is_item_running(context_id, item_id))
        if not can:
            return False, why
        from_phase = str(item.get("phase") or "")

        # 1. Sessions — hard-deleted with their transcripts, the same act Drop performs. The run
        #    trace they produced is preserved and labelled by `sessions.delete` itself.
        from ..app_state import sessions as _sessions
        session_ids = _dev.work_item_session_ids(item)
        for sid in session_ids:
            try:
                _sessions.delete(ctx, sid, cause="deleted")
            except Exception:
                log.warning("rerun %s: session %s could not be deleted", item_id, sid)

        # 2. Worktree — the DIR only. `remove_worktree` keeps the branch ref by design, and the
        #    dir must be gone BEFORE the re-cut: resetting a branch that is checked out somewhere
        #    would leave that tree lying about its own history.
        if item.get("git_worktree") or item.get("git_branch"):
            try:
                git_layer.remove_worktree(ctx.cwd, ctx.id, item_id)
            except Exception:
                log.warning("rerun %s: worktree removal failed (continuing)", item_id)

        # 3. Branch — re-cut to the sha it was cut from, old tip parked on a backup ref. This is
        #    the half that decides what the NEXT attempt builds on; best-effort, because a re-run
        #    must not fail on an item that never reached build (no branch, no base).
        recut = {"recut": False, "reason": "this item has no branch yet"}
        if item.get("git_branch"):
            try:
                recut = git_layer.recut_branch(ctx.cwd, item_id, str(item["git_branch"]),
                                               str(item.get("git_base") or ""))
            except Exception:
                log.warning("rerun %s: branch re-cut failed (continuing)", item_id)
                recut = {"recut": False, "reason": "re-cut failed — see the daemon log"}

        # 4. Rows — SOFT delete (never a real one). Stamped BEFORE the `item.rerun` event below,
        #    so that event survives unstamped and the re-run stays on the item's own trail.
        stamped_at = _now()
        trace = _spine.discard_item_trace(context_id, item_id, at=stamped_at)
        trace["dev_events"] = _dev_store.discard_item_events(context_id, item_id, at=stamped_at)

        # 5. The item's own files + frontmatter. Not best-effort: this IS the reset.
        reset = _dev.reset_work_item(dev_root, item_id)
        if reset is None:
            return False, "the item's `item.md` could not be read — nothing was reset"

        _dev_store.log_event(
            context_id, "item.rerun",
            f"Re-ran from {from_phase or 'the start'} — cleared and restarted",
            item_id=item_id, actor="owner",
            meta={"from_phase": from_phase, "removed": reset["removed"],
                  "sessions_deleted": len(session_ids),
                  "runs_discarded": trace["runs"], "run_events_discarded": trace["events"],
                  "dev_events_discarded": trace["dev_events"],
                  **({"branch_recut_from": recut.get("from_sha"),
                      "branch_backup_ref": recut.get("backup_ref")} if recut.get("recut") else
                     {"branch_recut": False, "branch_recut_skipped": recut.get("reason")})})

        # 4. Fire the entry phase — unless upstreams hold it, in which case the scheduler owns it.
        if reset["status"] != "active":
            log.info("rerun %s: reset, parked at %s", item_id, reset["status"])
            return True, f"reset, held at `{reset['status']}` until its upstreams land"
        started, detail = _fire(ctx, context_id, item_id, reset["phase"])
        if not started:
            # The reset stands — the item is genuinely back at the start — but nothing is running,
            # and saying so is the point. Resume is the way forward from here.
            _dev.set_work_item_error(
                dev_root, item_id,
                f"re-run reset the item but the {reset['phase']} run did not start — {detail}")
            log.warning("rerun %s: reset ok, fire failed (%s)", item_id, detail)
            return False, f"reset, but no run started — {detail}"
        log.info("rerun %s: reset and %s", item_id, detail)
        return True, f"reset and {detail}"
    except Exception:
        log.exception("rerun failed for %s", item_id)
        return False, "re-run failed — see the daemon log"
