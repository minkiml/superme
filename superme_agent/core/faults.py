"""Why a turn failed — the one reader.

`none` ran · `transient` upstream blipped, wait on the ladder · `rate_limited` the window is
spent, the cure is time · `fault` our bug, never retried. Unrecognized reads as `fault`.
"""

import asyncio
import errno
import re
from dataclasses import dataclass


# Seven attempts over ~29 minutes — long enough for a blip, short enough to surface a real outage.
RETRY_LADDER: tuple[int, ...] = (60, 180, 300, 300, 300, 300, 300)

# `retry-after` wins when given, but capped: an unbounded honour parks a run for a day.
_RATE_FLOOR = 300
_RATE_CAP = 3600


def next_delay(attempt: int) -> int | None:
    """Seconds to wait before attempt `attempt` (0-based). None once the ladder is spent."""
    return RETRY_LADDER[attempt] if 0 <= attempt < len(RETRY_LADDER) else None


@dataclass(frozen=True)
class Fault:
    """What went wrong, in the three words recovery paths branch on. `reason` is owner-facing prose —
    it lands on the Error label."""

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
        """Wait before `attempt`, or None when the ladder is spent. Rate limits wait for the WINDOW,
        not the ladder."""
        if not self.retryable:
            return None
        base = next_delay(attempt)
        if base is None:
            return None
        if self.kind == "rate_limited":
            return min(max(self.retry_after or _RATE_FLOOR, _RATE_FLOOR), _RATE_CAP)
        return base


NO_FAULT = Fault("none")


# Anchored at the START: an agent writing about an error mid-paragraph must not read as one.
_API_ERROR_REPLY = re.compile(r"^\s*API Error:?\s*(\d{3})\b", re.I)

# HTTP statuses worth waiting on. 529 is Anthropic's "Overloaded"; the 52x band is Cloudflare's.
_TRANSIENT_STATUS = {500, 502, 503, 504, 520, 521, 522, 523, 524, 529}
_RATE_STATUS = {429}

# The same two conditions as prose, for failures that arrive without a status code.
_TRANSIENT_TEXT = re.compile(
    r"overloaded|service unavailable|bad gateway|gateway time-?out|temporarily unavailable"
    r"|internal server error|connection (reset|refused|closed|aborted|error)|server disconnected"
    r"|broken pipe|timed? ?out|eof occurred|network is (unreachable|down)"
    r"|name or service not known|cannot connect|failed to establish|remote end closed",
    re.I)
# The client's own words for a spent window arrive as prose, with no status code anywhere.
_RATE_TEXT = re.compile(r"rate[ _-]?limit|usage limit|session limit|limit reached"
                        r"|too many requests|quota exceeded", re.I)
# A limit naming a wall-clock reset ("resets 5pm") carries no seconds, so the floor decides.

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
    """The first line, trimmed — enough for a label to name the failure, not its category."""
    return " ".join(text.strip().splitlines()[0].split())[:160]


def _from_text(text: str, *, source: str) -> Fault:
    """Classify a message body the same way whether it came from an exception or a reply."""
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
    """The single entry point: an exception and the turn's last assistant reply.

    The SDK returns some upstream errors as assistant TEXT, so a reply can itself be a failure."""
    if exc is not None:
        # A cancellation is the daemon shutting down, not a failure of the work.
        if isinstance(exc, asyncio.CancelledError):
            return Fault("transient", "the run was cancelled (daemon shutting down)")
        if isinstance(exc, OSError) and exc.errno in _TRANSIENT_ERRNO:
            return Fault("transient", f"the connection dropped ({exc.strerror or exc.errno})")
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return Fault("transient", "the connection timed out or dropped")
        text = f"{type(exc).__name__}: {exc}"
        # ProcessError points at a stderr the SDK never piped. Splice the captured tail in.
        if "exit code" in text.lower() or "stderr" in text.lower():
            from .agent_service import cli_stderr_tail
            if (tail := cli_stderr_tail(6)):
                text = f"{text} — CLI stderr: {' | '.join(tail.splitlines())}"
        return _from_text(text, source="crash")
    if reply and not did_work:
        text = reply.strip()
        m = _API_ERROR_REPLY.match(text)
        if m:
            return _from_text(text[:400], source="upstream error, no work done")
        # `_from_text` falls back to `fault`, so only a reply that NAMES a limit counts here.
        if _RATE_TEXT.search(text):
            return Fault("rate_limited", f"the usage window is spent — {_detail(text)}",
                         _retry_after(text))
    return NO_FAULT
