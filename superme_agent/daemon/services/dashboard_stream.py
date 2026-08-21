"""The dashboard's live-push channel.

It carries TOPICS, never values: a frame says what changed, the browser refetches over HTTP, and
every number keeps one source. Best-effort — a full queue drops the frame rather than backpressure
the write.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("superme-agent")

# One queue per open panel, small: a stalled client should drop rather than buffer history it will
# refetch.
_subscribers: set[asyncio.Queue] = set()
_QUEUE_MAX = 64

# A build cycle writes a burst of events; collect topics for a beat, then emit ONE frame carrying
# the union.
COALESCE_MS = 250
_COALESCE_S = COALESCE_MS / 1000
_pending: set[str] = set()
_flush_task: asyncio.Task | None = None

# The topic every system-wide number lives under. A run starting moves totals, the feed and the
# roster.
TOPIC_SYSTEM = "sys:"


def topics_for(event: dict) -> list[str]:
    """The invalidation topics one dev event implies.

    Deliberately coarse — the repo prefix plus `sys:`. Being finer would mean this module deciding
    which FE views care about which event kinds, a coupling that rots the first time either changes."""
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
    """Queue `topics` for the next coalesced frame.

    Safe to call from anywhere on the daemon's event loop, and a no-op when nobody is watching."""
    if not _subscribers or not topics:
        return
    _pending.update(topics)
    _schedule_flush()


def publish_event(event: dict) -> None:
    """The `log_event` observer: one event to its topics.

    It runs on whatever context the write happened on, so it never touches the queues directly."""
    publish(topics_for(event))


def _schedule_flush() -> None:
    global _flush_task
    if _flush_task and not _flush_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No loop here, so topics ride out with the next publish that lands on one. The backstop
        # covers it.
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
