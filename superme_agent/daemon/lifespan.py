"""Daemon lifespan — startup hygiene and background loops.

On entry it reconciles orphaned runs and launches the idle-sweep heartbeat; on exit it cancels the
loop cleanly.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import app_state
from ..core import dev_store, git_layer
from ..gateway import contexts
from .services import dashboard_stream, watchdog
from .services.learning import idle_sweep_loop, SWEEP_POLL_SECONDS, SWEEP_IDLE_SECONDS

log = logging.getLogger("superme-agent")


def _backfill_session_stamps() -> None:
    """One-time migration: stamp `session.item_id` for every work-item that already carries a
    `session_id`. Write-once, so it never clobbers a real stamp."""
    try:
        pairs: list[tuple[str, str]] = []
        for rid in app_state.spine.repos():
            ctx = contexts.resolve(rid, "dev")
            if not ctx.internal_root:
                continue
            try:
                data = app_state.dev.read_all(ctx.internal_root / "dev")
            except Exception:
                continue
            for it in data.get("work_items", []):
                if not it.get("id"):
                    continue
                for sid in app_state.dev.work_item_session_ids(it):  # every role thread + legacy
                    pairs.append((sid, it["id"]))
        if pairs:
            n = app_state.spine.backfill_session_items(pairs)
            if n:
                log.info("backfilled work-item stamp onto %d session(s)", n)
    except Exception:
        log.exception("session-stamp backfill failed (non-fatal)")


def _reconcile_orphaned_sessions() -> None:
    """Retire sessions whose work-item no longer exists on disk.

    No disposal path covers a folder that leaves out of band, leaving a composer over nothing."""
    try:
        for rid in app_state.spine.repos():
            ctx = contexts.resolve(rid, "dev")
            if not ctx.internal_root:
                continue
            try:
                data = app_state.dev.read_all(ctx.internal_root / "dev")
            except Exception:
                continue          # unreadable tree — say nothing about this repo's sessions
            live = {str(it.get("id")) for it in (data.get("work_items") or []) if it.get("id")}
            if not live:
                continue          # a repo with no items at all: nothing to reconcile against
            for s in app_state.spine.sessions_for_repo(rid):
                iid = str(s.get("item_id") or "")
                if not iid or iid in live:
                    continue
                try:
                    app_state.sessions.delete(ctx, str(s.get("id")), cause="retired")
                    log.info("retired session %s — its work-item %s no longer exists",
                             str(s.get("id"))[:8], iid)
                except Exception:
                    log.exception("orphan session retire failed %s", s.get("id"))
    except Exception:
        log.exception("orphaned-session reconciliation failed (non-fatal)")


def _reconcile_expired_transcripts() -> None:
    """Retire sessions whose TRANSCRIPT is gone.

    The CLI collects transcripts on a retention clock, so every session eventually meets it. Only
    ever acts on a missing file."""
    try:
        for rid in app_state.spine.repos():
            ctx = contexts.resolve(rid, "dev")
            for s in app_state.spine.sessions_for_repo(rid):
                sid = str(s.get("id") or "")
                if not sid or app_state.sessions.has_transcript(ctx, sid):
                    continue
                try:
                    app_state.sessions.delete(ctx, sid, cause="retired")
                    log.info("retired session %s — its transcript has expired", sid[:8])
                except Exception:
                    log.exception("expired-transcript retire failed %s", sid)
    except Exception:
        log.exception("expired-transcript reconciliation failed (non-fatal)")


def _report_orphaned_repos() -> None:
    """Say out loud when a repo's work is on disk but its registry entry is not.

    A lost entry looks exactly like a repo nobody connected."""
    try:
        for row in app_state.spine.orphaned_repos():
            log.warning("registry: '%s' has work on disk but no entry in repos.yaml — %s. "
                        "Reconnect it, or restore from superme_agent/config/repos-backups/",
                        row["repo_id"], ", ".join(row["evidence"]))
    except Exception:
        log.exception("orphaned-repo check failed (non-fatal)")


def _reconcile_worktrees() -> None:
    """Reconcile recorded worktrees against disk and branches, per repo.

    Heals a kill-mid-create, reports what it cannot fix, and finishes a terminal cleanup a dying daemon
    dropped."""
    try:
        for rid in app_state.spine.repos():
            ctx = contexts.resolve(rid, "dev")
            if not ctx.internal_root:
                continue
            try:
                items = app_state.dev.read_all(ctx.internal_root / "dev").get("work_items", [])
            except Exception:
                continue
            live: dict[str, dict] = {}
            stale_terminal: list[str] = []
            for it in items:
                if not it.get("git_worktree"):
                    continue
                if it.get("done_at") or str(it.get("status")) == "done":
                    stale_terminal.append(it["id"])
                else:
                    live[it["id"]] = {"branch": it.get("git_branch"),
                                      "worktree": it.get("git_worktree")}
            if live:
                for a in git_layer.reconcile(ctx.cwd, rid, live):
                    log.warning("worktree reconcile [%s] %s: %s",
                                rid, a.get("action"), a.get("detail"))
            for item_id in stale_terminal:
                if git_layer.worktree_dir(rid, item_id).exists():
                    try:
                        git_layer.remove_worktree(
                            ctx.cwd, rid, item_id,
                            branch=(app_state.dev.read_work_item(ctx.internal_root / "dev", item_id)
                                    or {}).get("git_branch"))
                        log.info("worktree reconcile [%s]: removed terminal item %s's leftover dir",
                                 rid, item_id)
                    except Exception:
                        log.exception("could not remove terminal worktree for %s", item_id)
    except Exception:
        log.exception("worktree reconciliation failed (non-fatal)")


# Run features that ARE a phase's own background work; for everything else, re-running the phase
# re-runs nothing.
_AUTO_RESUME_FEATURES = {"triage", "plan", "build", "vet", "investigate", "review", "close"}
# A restart after an outage can strand a cohort, and firing all at once spends tokens nobody asked
# for.
_MAX_AUTO_RESUME = 3


def _reconcile_orphaned_items(orphans: list[dict]) -> None:
    """Heal the work-items whose run the spine just flipped to `aborted`.

    Two acts: label every orphan `error`, then resume those whose run was a phase's own."""
    # The WHOLE body is guarded: housekeeping must never be able to stop the daemon booting.
    from .services.resume import resume_item
    try:
        # What was actually running when the daemon died. Last writer wins on an item with two
        # orphaned rows.
        feature_of: dict[tuple[str, str], str] = {}
        items_by_repo: dict[str, set[str]] = {}
        for o in orphans:
            if o.get("item_id"):
                rid, iid = str(o["repo_id"]), str(o["item_id"])
                items_by_repo.setdefault(rid, set()).add(iid)
                feature_of[(rid, iid)] = str(o.get("feature") or "")
        resumed = 0
        deferred: list[str] = []
        for rid, ids in items_by_repo.items():
            try:
                ctx = contexts.resolve(rid, "dev")
                if not ctx.internal_root:
                    continue
                dev_root = ctx.internal_root / "dev"
                for item_id in sorted(ids):
                    try:
                        it = app_state.dev.read_work_item(dev_root, item_id)
                        if not it or it.get("done_at") or str(it.get("status")) == "done":
                            continue
                        phase = str(it.get("phase") or "current")
                        feature = feature_of.get((rid, item_id), "")
                        # Label first: it is what makes a failed resume land on a truthful state,
                        # not `active` with nothing running.
                        if app_state.dev.set_work_item_error(
                                dev_root, item_id,
                                f"a daemon restart stopped the {phase} run"):
                            app_state.dev_store.log_event(
                                rid, "run.orphaned", item_id=item_id,
                                summary=f"Run orphaned by a daemon restart — the {phase} run "
                                        f"stopped mid-flight")
                        if feature not in _AUTO_RESUME_FEATURES:
                            log.info("orphan reconcile [%s]: %s stopped at %s (%s) — left for "
                                     "your Resume", rid, item_id, phase, feature or "unknown")
                            continue
                        if resumed >= _MAX_AUTO_RESUME:
                            deferred.append(f"{rid}/{item_id}")
                            continue
                        started, why = resume_item(rid, item_id)
                        if started:
                            resumed += 1
                            log.info("orphan reconcile [%s]: auto-resumed %s at %s",
                                     rid, item_id, phase)
                        else:
                            log.info("orphan reconcile [%s]: %s held at error — %s",
                                     rid, item_id, why)
                    except Exception:
                        log.exception("orphan reconcile failed for %s/%s", rid, item_id)
            except Exception:
                log.exception("orphan reconcile failed for repo %s", rid)
        if deferred:
            log.warning("orphan reconcile: %d item(s) over the auto-resume cap (%d) and NOT "
                        "resumed — they carry `error` and a Resume button: %s",
                        len(deferred), _MAX_AUTO_RESUME, ", ".join(deferred))
    except Exception:
        log.exception("orphan reconciliation failed (non-fatal)")


def _reconcile_stranded_proposals() -> None:
    """Free the learning proposals a dead `write` run left mid-flight.

    `writing` is transient, so one left there is invisible to every queue. The honest reset is back
    to `proposed`."""
    try:
        freed = 0
        for rid in app_state.spine.repos():
            for p in app_state.dev_store.list_memory_proposals(rid, status="writing"):
                pid = p.get("id")
                if pid is None:
                    continue
                app_state.dev_store.set_proposal_status(pid, "proposed")
                app_state.dev_store.log_event(
                    rid, "write.orphaned",
                    f"Proposal #{pid} was mid-write when the daemon stopped — returned to the "
                    f"queue for you to approve again",
                    scope="dev", actor="daemon", meta={"proposal_id": pid})
                freed += 1
        if freed:
            log.info("proposal reconcile: %d stranded `writing` proposal(s) → proposed", freed)
    except Exception:
        log.exception("stranded-proposal reconciliation failed (non-fatal)")


def _reconcile_close_steps() -> None:
    """A terminal transition is an ordered, re-runnable step list, so a daemon dying mid-close leaves
    unfinished steps, healed here on the next start.

    The worktree step belongs to `_reconcile_worktrees`."""
    from ..core.vocab import status_router
    from .services import scheduler
    from .services.runs import render_execution_md
    try:
        for rid in app_state.spine.repos():
            ctx = contexts.resolve(rid, "dev")
            if not ctx.internal_root:
                continue
            dev_root = ctx.internal_root / "dev"
            try:
                items = app_state.dev.read_all(dev_root).get("work_items", [])
            except Exception:
                continue
            for it in items:
                if not (it.get("done_at") or str(it.get("status")) == "done"):
                    continue
                item_id = str(it["id"])
                try:
                    if it.get("outcome") == "completed" and \
                            not (dev_root / "work-items" / item_id / "artifacts" /
                                 "execution.md").exists():
                        app_state.dev.write_artifact(dev_root, item_id, "execution.md",
                                                     render_execution_md(rid, item_id, it))
                        log.info("close reconcile [%s]: re-snapshot execution.md for %s",
                                 rid, item_id)
                    # The row half alone: this is boot, so the task registry is empty and there is
                    # nothing to cancel.
                    freed = app_state.spine.release_item_runs(rid, item_id)
                    if freed:
                        log.info("close reconcile [%s]: released %d run(s) for terminal %s",
                                 rid, freed, item_id)
                    resume_id = status_router.parent_to_resume(items, it)
                    if resume_id and app_state.dev.set_work_item_status(dev_root, resume_id,
                                                                        "active"):
                        log.info("close reconcile [%s]: resumed paused parent %s", rid, resume_id)
                    # Peers parked on this terminal item. Catches a cohort stranded by a daemon
                    # death mid-release.
                    scheduler.release_downstream(app_state.dev, dev_root, app_state.dev_store,
                                                 rid, items, item_id, cause="reconcile")
                except Exception:
                    log.exception("close reconcile failed for %s/%s", rid, item_id)
    except Exception:
        log.exception("close reconciliation failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Flip runs orphaned by a previous daemon's exit. Here rather than at import, so `app_state`
    # stays side-effect-free.
    _orphans = app_state.spine.reconcile()
    # Re-pin the learning sub-agents' model to their tier's current concrete id. No-op when
    # already current.
    app_state.spine.reconcile_agent_models()
    # Migrate a legacy concrete override back to its tier alias, the canonical form, so a pick
    # tracks a tier bump.
    app_state.spine.reconcile_model_overrides()
    # One-time: stamp durable work-item identity onto pre-existing sessions (idempotent).
    _backfill_session_stamps()
    # Runs after the backfill, so a merely unstamped session is claimed by its item and never read
    # as an orphan.
    _reconcile_orphaned_sessions()
    # The third way a row outlives its subject: the retention clock expired the transcript out
    # from under it.
    _reconcile_expired_transcripts()
    # Reports rather than heals: which repo a lost entry belonged to is the owner's answer.
    _report_orphaned_repos()
    # heal recorded-worktree drift before any run reads a tree that is not there
    _reconcile_worktrees()
    # finish any ordered close steps a dying daemon dropped mid-transition
    _reconcile_close_steps()
    # Runs after the close reconcile, so an item mid-close-transition is finished by that pass,
    # not this one.
    _reconcile_orphaned_items(_orphans)
    # Free any learning proposal a dead `write` run left at `writing` — the one hole with no way
    # out.
    _reconcile_stranded_proposals()

    # Every state change worth showing already writes a dev event; this turns each into a cache-
    # invalidation topic.
    dev_store.subscribe_events(dashboard_stream.publish_event)

    task = asyncio.create_task(idle_sweep_loop())
    log.info("idle sweep loop started (every %ds, idle threshold %ds, auto-learning=%s)",
             SWEEP_POLL_SECONDS, SWEEP_IDLE_SECONDS, app_state.spine.get_learning_enabled())
    # The reconcilers above cover runs whose task died WITH the daemon; the watchdog covers one
    # still alive inside it.
    stall_task = asyncio.create_task(watchdog.watch_loop())
    log.info("stall watchdog started (every %ds, stall threshold %ds)",
             watchdog.POLL_SECONDS, watchdog.STALL_SECONDS)
    try:
        yield
    finally:
        task.cancel()
        stall_task.cancel()
