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
from .services import dashboard_stream, watchdog
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


def _reconcile_orphaned_sessions() -> None:
    """Retire sessions whose work-item no longer exists on disk.

    Every product path that disposes an item already takes its sessions with it — clearance at
    close, abandon, re-run, the Prompt X-ray teardown. What none of them covers is a folder that
    leaves OUT OF BAND: a playground reset, a hand-deleted directory, a restored backup. The
    session row survives, so the chat picker keeps offering a live composer over a thread whose
    item is gone — and it is offered under a title that falls back to the raw id twice over,
    because there is no item left to name it. Found live 2026-08-14 on `41c0bf90d16a`.

    Retired, not deleted-by-the-owner: the item ended, nobody chose to discard the conversation.
    `sessions.delete` preserves the run trace and stamps it `session_fate=retired` (never-delete-logs).

    THE READ MUST SUCCEED BEFORE ANYTHING IS DROPPED. A repo whose dev tree fails to read yields
    no live-id set, and treating that emptiness as "no items exist" would retire every session the
    repo has. On any read failure this skips the repo entirely."""
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


# The run features that ARE a phase's own background work, and so can be re-fired by re-running
# that phase (`services.resume` dispatches exactly these). Everything else that can orphan —
# `chat` (the owner was talking), `deputy` (a judgment, re-fired by the gate seam), `resolve`,
# `compact`, and the learning runs — is deliberately absent: re-running the PHASE would not be
# re-running what died, so those get the label and wait for the owner's click.
_AUTO_RESUME_FEATURES = {"triage", "plan", "build", "vet", "investigate", "review", "close"}
# How many items may auto-resume on one boot. A restart after a long outage can strand a whole
# cohort, and firing every one of them at once spends real tokens the owner never asked for at a
# moment they may not even be watching. Anything over the cap keeps its `error` label and its
# Resume button — and is LOGGED, because a silent cap reads as "everything resumed" when it didn't.
_MAX_AUTO_RESUME = 3


def _reconcile_orphaned_items(orphans: list[dict]) -> None:
    """Heal the work-items whose run the spine just flipped `running → aborted` (dogfood D3,
    recovery R3).

    `spine.reconcile()` heals the RUN row; without this the ITEM stays `active` with no run and
    nothing that will ever start one — permanently wedged, and until the attention engine learned
    the stall rule (D2), completely silent. Restarting the daemon mid-run is routine in this
    project, so this was a recurring way to lose an item.

    **Two acts, in order** (R3). First LABEL: every orphan becomes `error` carrying "a daemon
    restart stopped this", which is the honest state — the run stopped — and gives the owner a
    Resume button whatever happens next. Then RESUME: if the dead run was a phase's own background
    work, re-fire it through `services.resume`, the SAME path the owner's button uses. An automatic
    path that resumed different phases than the manual one would drift, and the drift would only
    show up during an outage.

    This replaces parking everything at `awaiting_human`. That was honest about the run being gone
    but wrong about what it meant: `awaiting_human` claims a DECISION is wanted, so a build stopped
    by a restart looked identical to one waiting on the owner's judgment, and the loop that was
    meant to be human-free needed a click to breathe again. Nothing about the work was lost — the
    branch, worktree and artifacts all stood — only the run had to start over.

    Terminal items are left alone; `_reconcile_close_steps` owns those. Best-effort and idempotent."""
    # The WHOLE body is guarded, like every reconciler here. Housekeeping must never be able to
    # stop the daemon booting — the first cut of this raised at an import and took startup down
    # with it, which is a far worse failure than the stranded item it was fixing.
    from .services.resume import resume_item
    try:
        # feature per (repo, item): what was actually running when the daemon died. Last writer
        # wins on the rare item with two orphaned rows — they cannot differ in phase.
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
                        # LABEL first, always — even when the resume below succeeds a moment later.
                        # It is what makes a failed resume land back on a truthful state instead of
                        # `active` with nothing running.
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
    """Free the learning proposals a dead `write` run left mid-flight (recovery R3).

    `writing` is a TRANSIENT status: the write runner sets it, then moves the proposal to `drafted`
    or — if the agent never staged anything — puts it back to `proposed` so the owner can re-approve.
    That second branch only runs if the runner reaches its own tail. A daemon that dies mid-write
    never does, so the proposal sits at `writing` forever: invisible to the owner's queue (which
    lists `proposed`), invisible to the drafted gate, and picked up by nothing. Verified 2026-07-30
    that no reconciler existed for it — the one hole in the learning pipeline with no way out.

    No run is re-fired here. A write is cheap to re-approve and the owner gates it anyway, so the
    honest reset is back to `proposed` — where it was before the approval that started the run."""
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
    _orphans = app_state.spine.reconcile()
    # Re-pin the learning sub-agents' `.md` model to their tier's current concrete id (so a
    # MODEL_TIERS bump auto-propagates to the files). No-op when already current.
    app_state.spine.reconcile_agent_models()
    # Migrate any legacy CONCRETE picker override (system default + per-repo) back to its tier alias —
    # the canonical DB form, so a saved pick auto-tracks a MODEL_TIERS bump. No-op when already alias.
    app_state.spine.reconcile_model_overrides()
    # One-time: stamp durable work-item identity onto pre-existing sessions (idempotent).
    _backfill_session_stamps()
    # …then the other direction: sessions pointing at an item that is no longer on disk. Runs after
    # the backfill so a session that was merely UNSTAMPED is claimed by its item first, and never
    # mistaken for an orphan.
    _reconcile_orphaned_sessions()
    # S4 git layer: heal recorded-worktree drift (kill-mid-create, deleted dirs, dropped terminal
    # cleanups) before any run can touch a tree.
    _reconcile_worktrees()
    # S6 close protocol: finish any ordered close steps a dying daemon dropped mid-transition.
    _reconcile_close_steps()
    # D3 + R3: label the NON-terminal items whose run died with the last daemon, then auto-resume
    # the ones whose dead run was a phase's own background work. Runs after the close reconcile so
    # an item that was mid-close-transition is finished by that pass first and never touched here.
    _reconcile_orphaned_items(_orphans)
    # R3: and free any learning proposal a dead `write` run left stranded at `writing` — the one
    # hole in that pipeline with no way out.
    _reconcile_stranded_proposals()

    # The dashboard push channel's ONE wiring point (routing-audit §7.6). Every state change worth
    # showing the owner already writes a dev event; this turns each into a cache-invalidation topic
    # for any open `/ws/dashboard` panel. Registered here rather than at import so a test or a script
    # that imports the daemon doesn't acquire a live observer.
    dev_store.subscribe_events(dashboard_stream.publish_event)

    task = asyncio.create_task(_idle_sweep_loop())
    log.info("idle sweep loop started (every %ds, idle threshold %ds, auto-learning=%s)",
             SWEEP_POLL_SECONDS, SWEEP_IDLE_SECONDS, app_state.spine.get_learning_enabled())
    # The stall watchdog: an item run that stops emitting for STALL_SECONDS is stopped, labelled
    # `error` and handed back through Resume. The restart reconcilers above cover runs whose task
    # died WITH the daemon; this one covers the run still nominally alive inside it.
    stall_task = asyncio.create_task(watchdog.watch_loop())
    log.info("stall watchdog started (every %ds, stall threshold %ds)",
             watchdog.POLL_SECONDS, watchdog.STALL_SECONDS)
    try:
        yield
    finally:
        task.cancel()
        stall_task.cancel()
