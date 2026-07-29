"""The dashboard's live-push channel (`/ws/dashboard`) — routing-audit §3.3 / §7.6.

**This channel carries TOPICS, never values.** A frame says "everything under `dev:my-repo:`
changed"; the browser then refetches over ordinary HTTP. That is the whole design, and it is what
makes push safe here: every number on screen still has exactly ONE source (the REST read), so a
pushed frame and a polled read can never disagree about a value. A channel that shipped the values
themselves would introduce that disagreement — the reason the audit originally deferred push — and
this one structurally cannot.

What it replaces: the browser asking "anything new?" every five seconds whether or not anything
happened. With this, the poll becomes a backstop at 30s (the FE raises its own intervals while the
socket is up, and drops back to 5s the moment it isn't), and changes land immediately.

Where the events come from: `dev_store.log_event` — the one write every meaningful state change
already funnels through, 78 call sites across 20 modules. `lifespan` registers the observer.

Delivery is best-effort, exactly like `item_stream`: a full subscriber queue drops the frame rather
than backpressure whatever produced the event. Dropping an invalidation costs at most one backstop
poll of staleness, which is precisely what the backstop is for.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("superme-agent")

# Every open dashboard panel is one queue. Small: frames are tiny and a stalled client should be
# dropping, not buffering minutes of history it will re-derive from one refetch anyway.
_subscribers: set[asyncio.Queue] = set()
_QUEUE_MAX = 64

# Coalescing. A single build cycle writes a burst of events (run start, several artifacts, report,
# phase advance); without this the browser would refetch once per event. Collect topics for a beat,
# then emit ONE frame carrying the union. 250ms is below the threshold where a UI reads as delayed
# and comfortably above a burst's internal spacing.
COALESCE_MS = 250
_COALESCE_S = COALESCE_MS / 1000
_pending: set[str] = set()
_flush_task: asyncio.Task | None = None

# The topic every system-wide number lives under. A run starting or ending moves token totals, the
# attention feed and the running-count on the roster, so every event touches it.
TOPIC_SYSTEM = "sys:"


def topics_for(event: dict) -> list[str]:
    """The invalidation topics one dev event implies.

    Deliberately coarse: the repo prefix, plus `sys:`. The FE's key grammar nests
    (`dev:<ctx>:` covers that repo's board, its attention feed and every open item under it), so a
    repo-level topic is already the right blast radius for anything an item does. Being finer would
    mean this module deciding which of the FE's views care about which event kinds — a coupling that
    would rot the first time either side changed. Refetching one extra cheap endpoint is the
    cheaper mistake.
    """
    ctx = str(event.get("context_id") or "").strip()
    out = [TOPIC_SYSTEM]
    if ctx:
        out.insert(0, f"dev:{ctx}:")
    return out


def subscribe() -> asyncio.Queue:
    """Register a panel; returns the queue its invalidation frames arrive on. The caller MUST
    `unsubscribe` the same queue when the socket closes."""
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def has_subscribers() -> bool:
    return bool(_subscribers)


def publish(topics: list[str]) -> None:
    """Queue `topics` for the next coalesced frame. Safe to call from anywhere on the daemon's event
    loop, and a no-op when nobody is watching — which is the common headless/autopilot case, so an
    unattended run pays nothing for this channel existing."""
    if not _subscribers or not topics:
        return
    _pending.update(topics)
    _schedule_flush()


def publish_event(event: dict) -> None:
    """The `dev_store.log_event` observer: one event → its topics. Installed once, by `lifespan`.

    Runs on whatever thread/context the write happened on, which is NOT always the event loop (a
    background runner may be off-thread), so it never touches the queues directly — `publish` only
    mutates a set, and the flush is scheduled on the loop.
    """
    publish(topics_for(event))


def _schedule_flush() -> None:
    global _flush_task
    if _flush_task and not _flush_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop here (a synchronous write off the daemon's loop). The topics stay pending and ride
        # out with the next publish that does land on the loop; the backstop poll covers the gap.
        return
    _flush_task = loop.create_task(_flush())


async def _flush() -> None:
    await asyncio.sleep(_COALESCE_S)
    topics = sorted(_pending)
    _pending.clear()
    if not topics:
        return
    frame = {"type": "invalidate", "topics": topics}
    for q in list(_subscribers):
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            log.debug("dashboard_stream: dropped invalidation for a slow panel")
