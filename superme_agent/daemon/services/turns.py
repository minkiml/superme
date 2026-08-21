"""One turn, with the retry ladder around it.

IT ONLY EVER RETRIES A TURN THAT DID NOTHING: an attempt that issued a tool call is never
replayed, because doubling those effects is worse than a wait.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable

from ...core import Status, TextDelta
from ...core.faults import RETRY_LADDER, Fault, NO_FAULT, classify
from . import run_tasks

log = logging.getLogger("superme-agent")


class ResilientTurn:
    """A single logical turn that may be attempted several times. Not reusable — one instance per
    turn, so `fault` and `attempts` describe that turn and nothing else."""

    def __init__(self, label: str, *, item_id: str | None = None, retry: bool = True,
                 notify: Callable[[Fault, int, int], None] | None = None):
        self.label = label
        self.item_id = item_id
        self.retry = retry
        # The runner leaves a trail: a run silently asleep for half an hour looks exactly like a
        # hung one.
        self._notify = notify
        self.fault: Fault = NO_FAULT
        self.attempts = 0          # completed attempts, so 1 after a clean first try

    async def stream(self, agent, ctx, prompt: str, **kw) -> AsyncIterator:
        """Yield the turn's events, re-attempting an attempt that failed having done nothing.

        Registers the task against the item here rather than in each runner, so the stall
        watchdog has something to cancel."""
        watch = run_tasks.register(getattr(ctx, "id", ""), self.item_id)
        try:
            async for ev in self._attempts(agent, ctx, prompt, **kw):
                yield ev
        finally:
            run_tasks.release(watch)

    async def _attempts(self, agent, ctx, prompt: str, **kw) -> AsyncIterator:
        """The ladder itself — `stream` wraps this with the task registration."""
        attempt = 0
        while True:
            saw_call = False
            last_text: str | None = None
            fault = NO_FAULT
            try:
                async for ev in agent.run_turn(ctx, prompt, **kw):
                    if isinstance(ev, Status):
                        saw_call = True
                    elif isinstance(ev, TextDelta):
                        last_text = ev.text
                    yield ev
            except asyncio.CancelledError:
                # The daemon is going down. Not our failure to classify, and not ours to swallow.
                raise
            except Exception as exc:  # noqa: BLE001 — classification is the whole point
                fault = classify(exc=exc)
                log.exception("%s turn failed (%s)%s", self.label, fault.kind,
                              f" for {self.item_id}" if self.item_id else "")
            else:
                # No exception — but an upstream error handed back as text is still a failed turn.
                fault = classify(reply=last_text, did_work=saw_call)
                if fault.failed:
                    log.warning("%s turn produced no work, only an upstream error (%s): %s",
                                self.label, fault.kind, fault.reason)
            self.attempts = attempt + 1
            self.fault = fault
            if not fault.failed:
                return
            # The safety rule: a turn that touched anything is never replayed.
            delay = fault.delay(attempt) if (self.retry and not saw_call) else None
            if delay is None:
                return
            if self._notify:
                try:
                    self._notify(fault, attempt + 1, delay)
                except Exception:
                    log.exception("retry notify failed for %s", self.label)
            log.info("%s: %s — waiting %ds before retry %d/%d",
                     self.label, fault.reason, delay, attempt + 1, len(RETRY_LADDER))
            await asyncio.sleep(delay)
            attempt += 1
