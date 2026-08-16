"""The stall watchdog — a run that stops producing anything is stopped, labelled, and offered back.

**The incident this exists for** (2026-08-14, hub item `40663a5fbc96`). A research run spawned five
Explore subagents. Two minutes in, every one of them fell silent within three seconds of each
other; the run then sat `running` for another twenty-four minutes with zero events and finished
only because a daemon restart aborted it — 452k tokens spent, nothing produced. A sibling run on
the same repo worked straight through that window, so it was not an account-wide limit. The cause
is still unproven; what is certain is that NOTHING in SuperMe noticed. The item read IN PROGRESS
with a live timer the whole time, which is the same lie R2 was built to end for dead runs.

**The rule.** An item run whose last sign of life is older than `STALL_SECONDS` is stalled. Sign of
life = its newest `run_event` (a tool call, a result, a reply — subagent activity included, since
those are recorded too) or, before any of those, its own `started_at`. Deliberately NOT tokens: the
live token bump writes the same row the watchdog reads, and a frozen stream stops both anyway.

**What it does — and pointedly does not.** Cancel the task, close the run row `aborted`, mark the
item `error` with the silence named, log `run.stalled`. It does NOT resume: a stall of unknown
cause re-fired automatically is a loop that burns the window twice as fast, and R4's Resume button
is already sitting there for a person who has seen the reason. The restart reconciler auto-resumes
because a daemon restart is a KNOWN, harmless cause; this one is not.

**Only item runs.** A chat turn has a human watching it and a Stop button; an unattended background
run has neither, and it is the one that went silent for twenty-four minutes.

**`STALL_SECONDS` is TWENTY MINUTES, and that number was PAID FOR.** It was set to one minute on
2026-08-14 and killed a healthy hub audit run 70 seconds into a synthesis pause — six subagents had
returned and the agent was reasoning over their findings, which emits no events. 398k tokens of
finished reading, discarded. The lesson is the shape of the signal, not the size of the number: an
empty trail cannot distinguish thinking from death, so this is a BACKSTOP for the pathological case
and must never be tuned down into a monitor.

The three measurements that bound it: a healthy run's gap between events is ~2 seconds · a
post-fan-out synthesis pause is ~70 seconds · the incident this exists for was 24 minutes. Twenty
sits above everything legitimate that has ever been measured here and still catches the case that
cost 452k tokens.

For earlier detection the answer is TWO TIERS, never a smaller number: a run sitting between calls
is idle and could trip fast, while a run whose last event is an unmatched `tool` row is inside a
command and keeps the long bar. Not built, and note it would NOT have caught the original incident
either — that run's last event was a `tool` row, so it read as 'inside a command' for all 24
minutes.
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

# Both are env-overridable ONLY so the thing can be proved on a real run instead of a simulated
# one: at 20 minutes a live test costs 20 minutes of waiting, so the first version of this was
# going to be a stubbed sweep over a fake row — which would have tested the arithmetic and not the
# act. `SUPERME_STALL_SECONDS=60 SUPERME_STALL_POLL=15` makes the same code path fire in a minute
# against a genuine run, which is what actually catches a wiring mistake. Not a tuning knob: the
# defaults are the policy, and a daemon started without these is the daemon that ships.
STALL_SECONDS = int(os.environ.get("SUPERME_STALL_SECONDS") or 20 * 60)
POLL_SECONDS = int(os.environ.get("SUPERME_STALL_POLL") or 120)


def _age_seconds(stamp: str | None) -> float | None:
    """Seconds since an ISO stamp from the spine, or None if it can't be read. Naive stamps are
    read as UTC — that is what `_now()` writes, and guessing local time on one would invent a
    seven-hour stall or hide a real one."""
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
    """The in-flight item runs that have gone quiet for longer than `stall_seconds`.

    Pure over the spine read — the sweep's decision, separated from its effects so the rule can be
    tested without a daemon, a live run, or a twenty-minute wait."""
    out = []
    for r in _spine.live_item_runs_quiet_since():
        quiet = _age_seconds(r.get("quiet_since"))
        if quiet is not None and quiet >= stall_seconds:
            out.append({**r, "quiet_seconds": int(quiet)})
    return out


def stop_stalled_run(run: dict) -> bool:
    """Stop ONE stalled run: cancel · close the row · label the item · leave the trail.

    Returns True if the item was labelled. Best-effort throughout — this is housekeeping, and a
    watchdog that raises takes the poll loop down with it, which is worse than the stall."""
    repo_id, item_id = str(run.get("repo_id") or ""), str(run.get("item_id") or "")
    phase = str(run.get("phase") or run.get("feature") or "")
    quiet = int(run.get("quiet_seconds", 0))
    # Say the number in the unit that carries it. Integer minutes read as "0 minutes" below one,
    # which is a sentence that tells the owner nothing and reads like a bug (seen live, 2026-08-14,
    # while proving this against a short threshold).
    span = f"{quiet // 60} minutes" if quiet >= 60 else f"{quiet} seconds"
    if not (repo_id and item_id):
        return False
    reason = (f"the {phase} run stopped producing output for {span} and was stopped "
              f"— nothing was written in that time. Resume re-enters the phase on its own thread; "
              f"the work already on disk stands.") if phase else \
             (f"the run stopped producing output for {span} and was stopped")
    try:
        # 1. The task first. Closing the row while the frozen turn still holds it would let Resume
        #    start a second run on top of the first.
        cancelled = run_tasks.cancel(repo_id, item_id)
        # 2. The row — the watchdog closes it ITSELF rather than trusting the cancellation to land:
        #    a task frozen inside a syscall may never reach a suspension point, and the run-lock
        #    must open either way. A zombie that later finishes finds no running row and no-ops.
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
        # A terminal item has nothing to label and no gate to return to — skip rather than
        # resurrect it as `error` (clearance's own release path owns those rows).
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
