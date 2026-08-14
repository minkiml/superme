"""Why a turn failed — the ONE reader of that question (recovery-resilience R1).

Before this module every runner answered it locally and differently: `loop.py` matched an
"API Error: 529" reply with `is_infra_reply` and called it a fault, everyone else caught
`Exception` and called it a crash, and nothing anywhere could tell "Anthropic was overloaded for
ninety seconds" apart from "we passed a bad argument". Those two need opposite handling — the first
wants a wait, the second wants a human — so collapsing them meant the loop's only honest answer was
to page the owner for both. That is the defect this closes.

**Three kinds, plus the absence of one.**

    none          the turn ran. Nothing here failed.
    transient     upstream was unreachable or refused us for a moment — 5xx, "Overloaded", a
                  dropped socket, a timeout. Nothing is wrong with the work or with us. Wait on
                  the ladder and try again.
    rate_limited  the account's usage window is spent (429, "usage limit"). Also not a defect;
                  the cure is time, and specifically the window's own time, not ours.
    fault         everything else — a bad argument, a permission gap, a broken invariant. OUR
                  bug. Retrying it just reproduces it, so it never enters the ladder.

**The classification is deliberately conservative.** Unrecognized means `fault`, never `transient`:
a mystery failure retried seven times is seven identical mysteries and half an hour lost, whereas a
transient failure mislabelled `fault` merely surfaces to the owner one wait too early. The cost is
asymmetric, so the default leans to the cheap mistake.

**Why a reply string can be a failure at all.** The SDK sometimes hands an upstream error back as
assistant TEXT rather than raising — "API Error: 529 Overloaded". No exception is raised, so the
turn looks clean: the run got stamped `outcome=success` with 0 tokens and the loop happily vetted a
build cycle that never ran (build run 804, 2026-07-30). `classify(reply=...)` catches that shape,
but only when `did_work` is False — an agent that ran tools and then WROTE about an API error is
reporting, not failing.

Pure: no I/O, no clock, no sleeping. The runner that owns the turn owns the waiting.
"""

import asyncio
import errno
import re
from dataclasses import dataclass

# --------------------------------------------------------------------------- the ladder

# The owner's schedule (2026-07-31): "retry at 1 min, and then 3 min and check and retry every 5
# minutes for 5 times then no more retry." Seven attempts spanning ~29 minutes, which is the right
# order of magnitude for an upstream blip and short enough that a genuine outage surfaces to the
# owner the same working session rather than the next day.
RETRY_LADDER: tuple[int, ...] = (60, 180, 300, 300, 300, 300, 300)

# A usage window never reopens in sixty seconds, so a rate-limited retry ignores the head of the
# ladder and waits at least this long. When the upstream tells us how long (`retry-after`), that
# number wins — capped, because an unbounded honour of a server-supplied delay is a way to park a
# run for a day on one malformed header.
_RATE_FLOOR = 300
_RATE_CAP = 3600


def next_delay(attempt: int) -> int | None:
    """Seconds to wait before attempt `attempt` (0-based: 0 is the first retry, after the original
    try failed). None once the ladder is spent — the caller stops and hands the item over."""
    return RETRY_LADDER[attempt] if 0 <= attempt < len(RETRY_LADDER) else None


# --------------------------------------------------------------------------- the verdict

@dataclass(frozen=True)
class Fault:
    """What went wrong, in the three words the recovery paths actually branch on.

    `reason` is owner-facing prose — it lands on the item's Error label and in the dev event, so it
    says what happened, not which regex matched. `retry_after` is the upstream's own hint in
    seconds when it gave one (429s usually do), None otherwise."""

    kind: str          # none | transient | rate_limited | fault
    reason: str = ""
    retry_after: int | None = None

    @property
    def failed(self) -> bool:
        return self.kind != "none"

    @property
    def retryable(self) -> bool:
        """Whether waiting could plausibly fix this. `fault` is excluded on purpose: our own bug
        does not heal on a timer."""
        return self.kind in ("transient", "rate_limited")

    def delay(self, attempt: int) -> int | None:
        """How long to wait before `attempt`, or None when the ladder is spent / this kind never
        retries. Rate limits wait for the WINDOW (the upstream's hint, floored and capped); a
        transient failure walks the ladder."""
        if not self.retryable:
            return None
        base = next_delay(attempt)
        if base is None:
            return None
        if self.kind == "rate_limited":
            return min(max(self.retry_after or _RATE_FLOOR, _RATE_FLOOR), _RATE_CAP)
        return base


NO_FAULT = Fault("none")


# --------------------------------------------------------------------------- classification

# The SDK's own error prefix at the START of a reply. Anchored deliberately: an agent writing about
# an error mid-paragraph ("the 500 we saw earlier…") must never read as one.
_API_ERROR_REPLY = re.compile(r"^\s*API Error:?\s*(\d{3})\b", re.I)

# HTTP statuses worth waiting on. 529 is Anthropic's "Overloaded"; the 52x band is Cloudflare's.
_TRANSIENT_STATUS = {500, 502, 503, 504, 520, 521, 522, 523, 524, 529}
_RATE_STATUS = {429}

# Text shapes for the same two conditions, for the failures that arrive as prose rather than a code.
_TRANSIENT_TEXT = re.compile(
    r"overloaded|service unavailable|bad gateway|gateway time-?out|temporarily unavailable"
    r"|internal server error|connection (reset|refused|closed|aborted|error)|server disconnected"
    r"|broken pipe|timed? ?out|eof occurred|network is (unreachable|down)"
    r"|name or service not known|cannot connect|failed to establish|remote end closed",
    re.I)
# `session limit` and `usage limit reached` are the CLIENT's own words for a spent window, and they
# arrive as ordinary prose with no status code anywhere in them — observed live 2026-08-14 as
# "You've hit your session limit · resets 5pm (Pacific/Auckland)". A pattern that only knew the API's
# phrasing read that as a normal reply and filed the run as `done`.
_RATE_TEXT = re.compile(r"rate[ _-]?limit|usage limit|session limit|limit reached"
                        r"|too many requests|quota exceeded", re.I)
# NOT parsed: a limit that names a wall-clock reset ("resets 5pm") carries no number of seconds.
# `_retry_after` returns None and the ladder's own floor (300s) decides the wait — the right default,
# since the string gives no timezone and a guessed one schedules a retry either pointlessly early or
# hours late.

# `retry-after: 90`, `retry after 90 seconds`, `try again in 90s`.
_RETRY_AFTER = re.compile(r"(?:retry[- ]after|try again in)\D{0,12}?(\d{1,5})", re.I)

# Socket-level errnos that mean "the network moved under us", not "we asked for the wrong thing".
_TRANSIENT_ERRNO = {errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED, errno.EPIPE,
                    errno.ENETUNREACH, errno.ENETDOWN, errno.EHOSTUNREACH, errno.ETIMEDOUT}


def _retry_after(text: str) -> int | None:
    m = _RETRY_AFTER.search(text)
    if not m:
        return None
    try:
        return max(0, int(m.group(1)))
    except ValueError:
        return None


def _detail(text: str) -> str:
    """The first line of a message, trimmed — enough for an owner-facing label to name the actual
    failure instead of its category."""
    return " ".join(text.strip().splitlines()[0].split())[:160]


def _from_text(text: str, *, source: str) -> Fault:
    """Classify a message body — an exception's str() or an assistant reply — the same way, because
    the same upstream conditions reach us through both."""
    codes = {int(c) for c in re.findall(r"\b(\d{3})\b", text)}
    if codes & _RATE_STATUS or _RATE_TEXT.search(text):
        return Fault("rate_limited", f"the usage window is spent — {_detail(text)}",
                     _retry_after(text))
    if codes & _TRANSIENT_STATUS or _TRANSIENT_TEXT.search(text):
        return Fault("transient", f"upstream was unavailable — {_detail(text)}",
                     _retry_after(text))
    return Fault("fault", f"{source} — {_detail(text)}")


def classify(*, exc: BaseException | None = None, reply: str | None = None,
             did_work: bool = False) -> Fault:
    """The single entry point. Pass the exception a runner caught and/or the turn's last assistant
    reply; get back the one verdict every recovery path reads.

    `did_work` is the caller's honest answer to "did this turn call a tool or file a report". When
    True, the reply is never read as a failure — a turn that did real work and mentioned an error is
    reporting on one, not suffering one. It has no effect on `exc`: an exception is a failure
    whether or not work preceded it.
    """
    if exc is not None:
        # A cancellation is the daemon shutting the task down, not a failure of the work. Callers
        # should let it propagate, but if one hands it here, say so honestly rather than filing a
        # shutdown as our bug.
        if isinstance(exc, asyncio.CancelledError):
            return Fault("transient", "the run was cancelled (daemon shutting down)")
        if isinstance(exc, OSError) and exc.errno in _TRANSIENT_ERRNO:
            return Fault("transient", f"the connection dropped ({exc.strerror or exc.errno})")
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return Fault("transient", "the connection timed out or dropped")
        return _from_text(f"{type(exc).__name__}: {exc}", source="crash")
    if reply and not did_work:
        text = reply.strip()
        m = _API_ERROR_REPLY.match(text)
        if m:
            return _from_text(text[:400], source="upstream error, no work done")
        # A SPENT USAGE WINDOW usually arrives as plain prose, with no `API Error: 429` prefix and no
        # status code at all — the client says it in its own voice and the turn simply ends. Matched
        # here rather than by widening the prefix gate, because `_from_text`'s fallback is `fault`:
        # sending every short reply down that path would file ordinary answers as crashes. Only a
        # reply that NAMES a limit, from a turn that did no work, is read as one.
        # Found live 2026-08-14: a review run ended with "You've hit your session limit · resets 5pm",
        # was classified NO_FAULT, and the run was recorded `done` with zero tokens — so no `error`
        # status, no Resume, and the work-item sat at its gate with unfillable checks. A limit is the
        # most ordinary interruption this system meets; it must not be the one it cannot see.
        if _RATE_TEXT.search(text):
            return Fault("rate_limited", f"the usage window is spent — {_detail(text)}",
                         _retry_after(text))
    return NO_FAULT
