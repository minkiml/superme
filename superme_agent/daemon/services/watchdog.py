"""The stall watchdog — a run that stops producing anything is stopped, labelled, and offered back.

It does NOT resume: a stall of unknown cause re-fired burns the window twice as fast. A BACKSTOP,
never a monitor.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from ..app_state import dev as _dev, dev_store as _dev_store, spine as _spine
from ...gateway import contexts
from . import run_tasks

log = logging.getLogger("superme-agent")

# Env-overridable only so the sweep can be proved against a real run in a minute. Not a tuning
# knob.
STALL_SECONDS = int(os.environ.get("SUPERME_STALL_SECONDS") or 20 * 60)
POLL_SECONDS = int(os.environ.get("SUPERME_STALL_POLL") or 120)


def _age_seconds(stamp: str | None) -> float | None:
    """Seconds since an ISO stamp, or None if it cannot be read. Naive stamps are read as UTC, which is
    what `_now()` writes."""
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds()


def stalled_runs(stall_seconds: int = STALL_SECONDS) -> list[dict]:
    """The in-flight item runs quiet for longer than `stall_seconds`.

    Pure over the spine read, so the rule can be tested without a daemon or a twenty-minute wait."""
    out = []
    for r in _spine.live_item_runs_quiet_since():
        quiet = _age_seconds(r.get("quiet_since"))
        if quiet is not None and quiet >= stall_seconds:
            out.append({**r, "quiet_seconds": int(quiet)})
    return out


def stop_stalled_run(run: dict) -> bool:
    """Stop ONE stalled run: cancel, close the row, label the item, leave the trail.

    Best-effort throughout — a watchdog that raises takes the poll loop with it, which is worse than
    the stall."""
    repo_id, item_id = str(run.get("repo_id") or ""), str(run.get("item_id") or "")
    phase = str(run.get("phase") or run.get("feature") or "")
    quiet = int(run.get("quiet_seconds", 0))
    # Say the number in the unit that carries it: integer minutes read as "0 minutes" below one.
    span = f"{quiet // 60} minutes" if quiet >= 60 else f"{quiet} seconds"
    if not (repo_id and item_id):
        return False
    reason = (f"the {phase} run stopped producing output for {span} and was stopped "
              f"— nothing was written in that time. Resume re-enters the phase on its own thread; "
              f"the work already on disk stands.") if phase else \
             (f"the run stopped producing output for {span} and was stopped")
    try:
        # The task first: closing the row while the frozen turn holds it would let Resume start a
        # second run.
        cancelled = run_tasks.cancel(repo_id, item_id)
        # The watchdog closes the row itself: a task frozen in a syscall may never reach a
        # suspension point.
        _spine.finish_item_run(repo_id, item_id, run_status="aborted")
        # 3. The item — `error` + reason, which is what puts the Resume button in front of a person.
        ctx = contexts.resolve(repo_id, "dev")
        from .runs import mark_item_error
        marked = mark_item_error(ctx, repo_id, item_id, reason, phase=phase)
        _dev_store.log_event(
            repo_id, "run.stalled",
            f"Run stopped — no output for {span} during {phase or 'the run'}",
            item_id=item_id, actor="daemon",
            meta={"phase": phase, "quiet_seconds": int(run.get("quiet_seconds", 0)),
                  "run_id": run.get("id"), "task_cancelled": cancelled})
        log.warning("stall watchdog: stopped %s/%s after %s of silence (task_cancelled=%s)",
                    repo_id, item_id, span, cancelled)
        return marked
    except Exception:
        log.exception("stall watchdog: could not stop %s/%s", repo_id, item_id)
        return False


def sweep(stall_seconds: int = STALL_SECONDS) -> int:
    """One pass. Returns how many runs were stopped."""
    stopped = 0
    for run in stalled_runs(stall_seconds):
        # A terminal item has nothing to label and no gate to return to, so skip rather than
        # resurrect it.
        try:
            ctx = contexts.resolve(str(run.get("repo_id")), "dev")
            item = _dev.read_work_item(ctx.internal_root / "dev",
                                       str(run.get("item_id"))) if ctx.internal_root else None
            if item and (item.get("done_at") or str(item.get("status")) == "done"):
                continue
        except Exception:
            log.exception("stall watchdog: could not read %s", run.get("item_id"))
        if stop_stalled_run(run):
            stopped += 1
    return stopped


async def watch_loop() -> None:
    """The poll task (started in `lifespan`). Never exits on its own; cancelled at shutdown."""
    while True:
        try:
            await asyncio.sleep(POLL_SECONDS)
            stopped = sweep()
            if stopped:
                log.info("stall watchdog: stopped %d silent run(s)", stopped)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("stall watchdog sweep failed (non-fatal)")
