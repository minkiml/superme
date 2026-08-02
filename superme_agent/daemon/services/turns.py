"""One turn, with the retry ladder around it (recovery-resilience R1).

Every background runner in the daemon has the same three lines at its heart — open the stream,
forward the events, catch whatever escapes — and until now each of them ended that story the same
poor way: `except Exception: turn_error = True`, which says a turn failed but never why, so the only
possible response was to page the owner. A 529 that would have cleared in ninety seconds cost the
same as a real bug.

`ResilientTurn` puts the ladder (`core.faults`) in one place instead of twelve:

    turn = ResilientTurn("vet", item_id=item_id)
    async for ev in turn.stream(_agent, ctx, prompt, **kw):
        ...                      # exactly the loop the runner had before
    if turn.fault.failed:        # …and one honest verdict afterwards
        ...

**It only ever retries a turn that did nothing.** The rule is `saw_call`: if this attempt issued a
single tool call, the attempt is not replayed, no matter how retryable the failure looks. Re-running
a turn that already edited files, committed, or filed a report would double those effects to save a
wait — a far worse outcome than surfacing the failure. So the ladder covers exactly the shape it was
built for (upstream refused us before any work happened) and declines everything else, where R4's
owner-driven Resume is the correct cure because a resumed session keeps the work already done.

**The empty-reply case is a failure here, not a success.** A turn whose whole output is
"API Error: 529 Overloaded" raises nothing; the stream ends normally. Left alone, the run is stamped
`outcome=success` with 0 tokens and the loop moves on as though a build cycle had happened — which
is precisely what run 804 did on 2026-07-30. Because that shape has no tool call by definition, it
is both detectable and safe to replay, so it is the one failure the ladder catches most cleanly.

Interactive turns pass `retry=False`: the owner is watching the stream, and a chat turn that sits
silent for twenty-nine minutes is worse than one that says "upstream is down" and offers the button.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable

from ...core import Status, TextDelta
from ...core.faults import RETRY_LADDER, Fault, NO_FAULT, classify

log = logging.getLogger("superme-agent")


class ResilientTurn:
    """A single logical turn that may be attempted several times. Not reusable — one instance per
    turn, so `fault` and `attempts` describe that turn and nothing else."""

    def __init__(self, label: str, *, item_id: str | None = None, retry: bool = True,
                 notify: Callable[[Fault, int, int], None] | None = None):
        self.label = label
        self.item_id = item_id
        self.retry = retry
        # Called before each wait with (fault, attempt-about-to-run, seconds). The runner uses it
        # to leave a trail — a run that is silently asleep for half an hour is indistinguishable
        # from a hung one, and the owner deserves to be able to tell those apart.
        self._notify = notify
        self.fault: Fault = NO_FAULT
        self.attempts = 0          # completed attempts, so 1 after a clean first try

    async def stream(self, agent, ctx, prompt: str, **kw) -> AsyncIterator:
        """Yield the turn's events, re-attempting on the ladder when an attempt fails without
        having done anything. Leaves `self.fault` set to the final verdict: NO_FAULT if some
        attempt got through, otherwise the failure the caller must act on."""
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
