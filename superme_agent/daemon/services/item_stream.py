"""In-process pub/sub of a work-item's live turn events, keyed by `item_id` (F2 unified timeline).

Every run's events already funnel through `runs.capture_event` — the ONE choke point shared by the
interactive turn (ws.py) and every background phase run (loop.py / runs.py). That choke also
publishes a framed event HERE, so any WebSocket panel *watching* an item receives its build/vet/
intake turns live — not only the turns that panel fired itself. This is what lets the autonomous
build & vet phases stream into the panel in real time.

Pure in-memory, single process (the runner and the WS handler share this module in one daemon), and
nothing is persisted — the durable record is the `run_event` trail. Delivery is best-effort: a full
subscriber queue (a stalled client) DROPS the frame rather than backpressure the runner; the panel
re-syncs the gap from the history endpoint. Publish/subscribe both run on the daemon's single event
loop, so `put_nowait` needs no locking.
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
    """Fan `frame` out to every panel watching `item_id`. Non-blocking and best-effort: a full queue
    (a stalled client) drops the frame — the panel's history refresh fills any gap — so a slow
    consumer can never backpressure the agent run producing the events."""
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
