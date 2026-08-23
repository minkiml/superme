"""In-process pub/sub of a work-item's live turn events, keyed by `item_id`.

Nothing is persisted — the durable record is the `run_event` trail. Delivery is best-effort: a full
subscriber queue drops the frame rather than backpressure the runner.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("superme-agent")

# item_id -> the set of subscriber queues currently watching it. A queue is one open panel.
_subscribers: dict[str, set[asyncio.Queue]] = {}

_QUEUE_MAX = 512   # per-panel buffer; a slower consumer drops frames (history refresh fills the gap)


def subscribe(item_id: str) -> asyncio.Queue:
    """Register a watcher for `item_id`; returns the queue its live frames arrive on. The caller
    MUST `unsubscribe` the same queue when the panel closes (a `finally` around the drain task)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _subscribers.setdefault(str(item_id), set()).add(q)
    return q


def unsubscribe(item_id: str, q: asyncio.Queue) -> None:
    """Drop one watcher; forget the item entirely once its last panel leaves (no idle key leak)."""
    subs = _subscribers.get(str(item_id))
    if subs:
        subs.discard(q)
        if not subs:
            _subscribers.pop(str(item_id), None)


def publish(item_id: str, frame: dict) -> None:
    """Fan `frame` out to every panel watching `item_id`.

    Best-effort: a full queue drops the frame, so a slow consumer cannot backpressure the run."""
    subs = _subscribers.get(str(item_id))
    if not subs:
        return
    for q in list(subs):
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            log.debug("item_stream: dropped frame for %s (slow consumer)", item_id)


def has_subscribers(item_id: str) -> bool:
    """True iff at least one panel is watching — lets `capture_event` skip framing work when nobody
    is listening (the common autopilot case with no panel open)."""
    return bool(_subscribers.get(str(item_id)))
