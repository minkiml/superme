"""Re-run a work-item — throw the work away and start it over.

IN PLACE, never a new id. The FILES are deleted, the ROWS soft-deleted so accounting still counts
them, and the BRANCH re-cut.
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
    """Can this item be re-run, and the one line saying why or why not. PURE, so the drilldown's action
    list and the route share it.

    Deliberately NOT conditioned on `error`: the point of re-run is that it is always there."""
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
    """Reset a work-item and re-fire its entry phase.

    Order matters: everything destructive happens BEFORE the fire. Teardown steps are individually
    best-effort, but the file reset is not — if `item.md` cannot be rewritten, nothing is fired."""
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

        # Sessions are hard-deleted with their transcripts; the run trace they produced is
        # preserved and labelled.
        from ..app_state import sessions as _sessions
        session_ids = _dev.work_item_session_ids(item)
        for sid in session_ids:
            try:
                _sessions.delete(ctx, sid, cause="deleted")
            except Exception:
                log.warning("rerun %s: session %s could not be deleted", item_id, sid)

        # The DIR only, and it must be gone BEFORE the re-cut: resetting a checked-out branch
        # leaves that tree lying.
        if item.get("git_worktree") or item.get("git_branch"):
            try:
                git_layer.remove_worktree(ctx.cwd, ctx.id, item_id)
            except Exception:
                log.warning("rerun %s: worktree removal failed (continuing)", item_id)

        # Re-cut to the sha it was cut from, old tip on a backup ref. Best-effort before any
        # build.
        recut = {"recut": False, "reason": "this item has no branch yet"}
        if item.get("git_branch"):
            try:
                recut = git_layer.recut_branch(ctx.cwd, item_id, str(item["git_branch"]),
                                               str(item.get("git_base") or ""))
            except Exception:
                log.warning("rerun %s: branch re-cut failed (continuing)", item_id)
                recut = {"recut": False, "reason": "re-cut failed — see the daemon log"}

        # Soft delete, stamped BEFORE the `item.rerun` event, so that event survives unstamped on
        # the item's trail.
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
            # The reset stands but nothing is running, and saying so is the point. Resume is the
            # way forward.
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
