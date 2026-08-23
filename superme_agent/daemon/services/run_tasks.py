"""Who is driving which item's run right now — the in-process task registry.

The run table says a run is in flight; this says which asyncio task holds it. Non-durable by
design.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("superme-agent")

_TASKS: dict[tuple[str, str], asyncio.Task] = {}

# `create_task` keeps only a WEAK reference, so an unheld task can be collected mid-await and
# nothing closes its row.
_ALIVE: set[asyncio.Task] = set()


def track(task: asyncio.Task) -> asyncio.Task:
    """Hold a strong reference to a fire-and-forget task until it completes.

    Never raises: this wraps the call that STARTS a run."""
    try:
        _ALIVE.add(task)
        task.add_done_callback(_reap)
    except (AttributeError, TypeError):
        _ALIVE.discard(task)
        log.debug("untrackable task object (%s) — passed through", type(task).__name__)
    return task


def _reap(task: asyncio.Task) -> None:
    """Release the reference and SHOUT if the task died of an exception.

    A fire-and-forget exception surfaces only when someone retrieves it, and nobody was."""
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
    """Bind the CURRENT task to (repo, item), returning the key to `release`.

    None when there is nothing to bind: a chat turn has a human watching it."""
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
    # Logged, because a missed registration and a key mismatch are indistinguishable after the
    # fact.
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

    Asked, not made to: cancellation lands at the next suspension point."""
    key = (str(repo_id), str(item_id))
    task = _TASKS.get(key)
    if task is None or task.done():
        if not expect_live:
            log.debug("no live task to cancel for %s/%s (%s)", key[0], key[1],
                      _how_it_ended(task) if task is not None else "never registered")
            return False
        # Say WHICH: "nobody registered" and "already finished" call for opposite fixes, and
        # cancelled-vs-raised is the diagnosis.
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
