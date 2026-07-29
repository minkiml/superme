"""Daemon lifespan — startup hygiene + background loops.

Replaces the deprecated `@app.on_event("startup")` hook with a modern `lifespan` context manager:
on entry it reconciles orphaned runs and launches the idle-sweep heartbeat; on exit it cancels the
loop cleanly. The heartbeat lives in `services.learning` (R4b) — a leaf module with no server.py
dependency, so this imports it directly (no cycle).
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import app_state
from ..core import dev_store, git_layer
from ..gateway import contexts
from .services import dashboard_stream
from .services.learning import _idle_sweep_loop, SWEEP_POLL_SECONDS, SWEEP_IDLE_SECONDS

log = logging.getLogger("superme-agent")


def _backfill_session_stamps() -> None:
    """One-time migration (work-item-session-recognition-prd): stamp `session.item_id` for every
    work-item that already carries a `session_id`, so in-progress items created before the stamp
    existed aren't stranded as 'general' sessions. Write-once, so it never clobbers a real stamp
    and is a no-op on subsequent starts. Best-effort — a bad repo must not block daemon startup."""
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


def _reconcile_worktrees() -> None:
    """Startup reconciliation (workspace-workflow S4/D4, nimbalyst punch-list): recorded
    worktrees vs disk vs branches, per repo. Heals a kill-mid-create (branch exists, dir missing
    → re-add), reports what it can't fix (missing branch, orphan dirs — never guesses, never
    deletes), and finishes a terminal cleanup a dying daemon dropped (item done, dir still on
    disk → remove; branch kept). Best-effort — a bad repo must never block daemon startup."""
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
                        git_layer.remove_worktree(ctx.cwd, rid, item_id)
                        log.info("worktree reconcile [%s]: removed terminal item %s's leftover dir",
                                 rid, item_id)
                    except Exception:
                        log.exception("could not remove terminal worktree for %s", item_id)
    except Exception:
        log.exception("worktree reconciliation failed (non-fatal)")


def _reconcile_close_steps() -> None:
    """Startup reconciliation of the CLOSE step list (S6/D8, nimbalyst archive crash-hole
    lesson): a terminal transition is an ordered, re-runnable step list — a daemon dying mid-close
    leaves a terminal item with unfinished steps, healed here on the next start. Per terminal
    item: execution.md snapshot present (re-renderable — run rows are kept forever) · run rows
    released · a paused parent whose last open blocking child went terminal resumed. The worktree
    step is _reconcile_worktrees' job. Idempotent + best-effort."""
    from ..core import status_router
    from .services import scheduler
    from .services.runs import _render_execution_md
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
                                                     _render_execution_md(rid, item_id, it))
                        log.info("close reconcile [%s]: re-snapshot execution.md for %s",
                                 rid, item_id)
                    freed = app_state.spine.release_item_runs(rid, item_id)
                    if freed:
                        log.info("close reconcile [%s]: released %d run(s) for terminal %s",
                                 rid, freed, item_id)
                    resume_id = status_router.parent_to_resume(items, it)
                    if resume_id and app_state.dev.set_work_item_status(dev_root, resume_id,
                                                                        "active"):
                        log.info("close reconcile [%s]: resumed paused parent %s", rid, resume_id)
                    # Peers parked on this terminal item (`after:`). The live routes already fire
                    # this; the reconcile catches a cohort stranded by a daemon death mid-release,
                    # which for an autopiloted launch is the difference between "resumes on its
                    # own" and "silently never runs again".
                    scheduler.release_downstream(app_state.dev, dev_root, app_state.dev_store,
                                                 rid, items, item_id, cause="reconcile")
                except Exception:
                    log.exception("close reconcile failed for %s/%s", rid, item_id)
    except Exception:
        log.exception("close reconciliation failed (non-fatal)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Flip any runs orphaned by a previous daemon's exit (running → aborted). Was at module import
    # before; doing it here keeps app_state import side-effect-free and runs it once per app start.
    app_state.spine.reconcile()
    # Re-pin the learning sub-agents' `.md` model to their tier's current concrete id (so a
    # MODEL_TIERS bump auto-propagates to the files). No-op when already current.
    app_state.spine.reconcile_agent_models()
    # Migrate any legacy CONCRETE picker override (system default + per-repo) back to its tier alias —
    # the canonical DB form, so a saved pick auto-tracks a MODEL_TIERS bump. No-op when already alias.
    app_state.spine.reconcile_model_overrides()
    # One-time: stamp durable work-item identity onto pre-existing sessions (idempotent).
    _backfill_session_stamps()
    # S4 git layer: heal recorded-worktree drift (kill-mid-create, deleted dirs, dropped terminal
    # cleanups) before any run can touch a tree.
    _reconcile_worktrees()
    # S6 close protocol: finish any ordered close steps a dying daemon dropped mid-transition.
    _reconcile_close_steps()

    # The dashboard push channel's ONE wiring point (routing-audit §7.6). Every state change worth
    # showing the owner already writes a dev event; this turns each into a cache-invalidation topic
    # for any open `/ws/dashboard` panel. Registered here rather than at import so a test or a script
    # that imports the daemon doesn't acquire a live observer.
    dev_store.subscribe_events(dashboard_stream.publish_event)

    task = asyncio.create_task(_idle_sweep_loop())
    log.info("idle sweep loop started (every %ds, idle threshold %ds, auto-learning=%s)",
             SWEEP_POLL_SECONDS, SWEEP_IDLE_SECONDS, app_state.spine.get_learning_enabled())
    try:
        yield
    finally:
        task.cancel()
