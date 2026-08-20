"""Who is driving which item's run, right now — the in-process task registry.

The run TABLE says a run is in flight; this says which asyncio task is holding it. They answer
different questions, and the stall watchdog needs both: the row tells it a run has gone quiet, and
this tells it what to cancel. Without the second half the watchdog could only relabel the item
while the frozen turn stayed alive underneath, and Resume would put a second run on top of it.

Deliberately dependency-free (no spine, no app_state, no core) so both ends can import it: the
turn runner registers, the watchdog cancels. In-process and non-durable BY DESIGN — a task cannot
outlive the daemon that owns it, and a restart's orphan reconciler (lifespan) is what covers runs
whose task died with the process.

One task per (repo, item) is the invariant, and it is not this module's to enforce: `_begin_run`'s
run-lock already refuses a second run for an item, so a second registration would mean that lock
was bypassed — it is logged, and the newer task wins (it is the one actually running).
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("superme-agent")

_TASKS: dict[tuple[str, str], asyncio.Task] = {}

# Every background task this daemon starts, held until it finishes. `asyncio.create_task` keeps only
# a WEAK reference, so a task nobody else holds can be garbage-collected mid-await — the documented
# footgun, and its signature is exactly what a run row stuck at `running` looks like from outside:
# the work stops, no exception is raised, and nothing closes the row.
#
# The per-item map above does not cover this. It is populated INSIDE the coroutine (first iteration
# of `ResilientTurn.stream`), so the window between `create_task` and that first step is unheld —
# and a fire-and-forget task that never reaches its first await point is precisely the one at risk.
_ALIVE: set[asyncio.Task] = set()


def track(task: asyncio.Task) -> asyncio.Task:
    """Hold a strong reference to a fire-and-forget task until it completes. Returns it, so a
    caller can write `run_tasks.track(asyncio.create_task(coro))` in place of the bare call.

    NEVER raises. This wraps the call that STARTS a run, so a failure here would mean a phase that
    refuses to begin — strictly worse than the collection risk it guards. Anything that doesn't
    behave like a Task (a test double, a future SDK type) is simply passed through untracked."""
    try:
        _ALIVE.add(task)
        task.add_done_callback(_reap)
    except (AttributeError, TypeError):
        _ALIVE.discard(task)
        log.debug("untrackable task object (%s) — passed through", type(task).__name__)
    return task


def _reap(task: asyncio.Task) -> None:
    """Release the reference and — the point of this — SHOUT if the task died of an exception.

    A fire-and-forget task's exception is only surfaced when someone retrieves it, and nobody was:
    three hub runs died silently on 2026-08-14 and the first sign of trouble each time was the
    stall watchdog twenty minutes later. `_how_it_ended` could only report the cause AFTER a stall
    made someone ask. This makes the death itself the event."""
    _ALIVE.discard(task)
    try:
        if task.cancelled():
            return
        exc = task.exception()
    except Exception:                              # noqa: BLE001 — never break a done-callback
        return
    if exc is not None:
        log.error("BACKGROUND TASK DIED — its run row is now orphaned until the watchdog stops it",
                  exc_info=exc)


def register(repo_id: str, item_id: str | None) -> tuple[str, str] | None:
    """Bind the CURRENT task to (repo, item). Returns the key to `release`, or None when there is
    nothing to bind (no item — a chat turn, which has a human watching it — or no running task)."""
    if not item_id:
        return None
    try:
        task = asyncio.current_task()
    except RuntimeError:          # no running loop: a synchronous test harness
        return None
    if task is None:
        return None
    key = (str(repo_id), str(item_id))
    if key in _TASKS and _TASKS[key] is not task:
        log.warning("two tasks registered for %s/%s — the run-lock should have refused one",
                    *key)
    _TASKS[key] = task
    # Logged because the ONE time this mattered it was unobservable: a live stall-kill reported
    # `task_cancelled: false` while the run was demonstrably mid-stream, and there was no way to
    # tell a missed registration from a key mismatch after the fact.
    log.info("run task registered: %s/%s (live: %d)", key[0], key[1], len(_TASKS))
    return key


def release(key: tuple[str, str] | None) -> None:
    """Drop a registration — only if it is still OURS. A late release from a task that was already
    replaced must not unregister the live one."""
    if key is None:
        return
    try:
        current = asyncio.current_task()
    except RuntimeError:
        current = None
    if _TASKS.get(key) is current or current is None:
        _TASKS.pop(key, None)


def cancel(repo_id: str, item_id: str, *, expect_live: bool = True) -> bool:
    """Cancel the task holding this item's run. True if one was found and asked to stop.

    Asked, not made to: cancellation lands at the task's next suspension point, so the caller must
    not treat this as "the run has stopped" — it closes the run row itself (see watchdog).

    `expect_live` is whether a task SHOULD be here. The watchdog only calls after detecting a live
    run, so an empty slot is an anomaly and gets shouted about. A disposal path (abandon, clearance,
    probe teardown) usually runs on an item that finished by itself, so nothing to cancel is the
    normal case and warning about it would bury the real ones."""
    key = (str(repo_id), str(item_id))
    task = _TASKS.get(key)
    if task is None or task.done():
        if not expect_live:
            log.debug("no live task to cancel for %s/%s (%s)", key[0], key[1],
                      _how_it_ended(task) if task is not None else "never registered")
            return False
        # Say WHICH — a bare False cannot distinguish "nobody registered" from "it already
        # finished", and those call for opposite fixes. When it IS done, say HOW it ended: a task
        # that is `done` while its registration is still present never ran its own `finally`, so
        # it did not finish normally, and cancelled-vs-raised is the whole diagnosis.
        log.warning("no live task to cancel for %s/%s (%s; registered: %s)", key[0], key[1],
                    _how_it_ended(task) if task is not None else "never registered",
                    sorted(_TASKS))
        return False
    task.cancel()
    return True


def _how_it_ended(task: asyncio.Task) -> str:
    if task.cancelled():
        return "already done — CANCELLED (something stopped the task; its run row was left open)"
    exc = task.exception()
    if exc is not None:
        return f"already done — RAISED {type(exc).__name__}: {exc}"
    return "already done — returned normally but never released its registration"


def live_keys() -> list[tuple[str, str]]:
    """Every (repo, item) currently registered — for tests and diagnostics."""
    return sorted(_TASKS)
