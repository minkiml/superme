"""The dashboard invalidation channel.

It carries TOPICS, never values, so a pushed frame cannot contradict a polled one. The frame's
SHAPE is asserted, because "a frame arrives" would pass on the dangerous version too.

Run: PYTHONPATH=. python -m scripts.test_dashboard_push
"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from superme_agent.core import dev_store as DS
from superme_agent.daemon.services import dashboard_stream as D
from scripts.sources import src

PASS = 0


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def test_topics() -> None:
    print("topics — one event maps to its repo + the system bucket")
    t = D.topics_for({"context_id": "test-playground", "kind": "run.report", "item_id": "abc"})
    ok("a repo's event invalidates that repo's topic", "dev:test-playground:" in t)
    ok("...and the system numbers it also moves (tokens · attention · roster counts)",
       D.TOPIC_SYSTEM in t)
    # The FE key grammar nests, so a repo-level topic already reaches every open item.
    ok("no per-item topic: the repo prefix already covers the item keys nested under it",
       not any(":item:" in x for x in t))
    ok("a repo-less event still invalidates the system bucket", D.topics_for({}) == [D.TOPIC_SYSTEM])


async def test_frame_carries_topics_only() -> None:
    print("the frame — topics, never values (the property the whole design rests on)")
    q = D.subscribe()
    try:
        D.publish(["dev:r1:", "sys:"])
        frame = await asyncio.wait_for(q.get(), timeout=2)
        ok("frame type is `invalidate`", frame["type"] == "invalidate")
        ok("it carries the topics", sorted(frame["topics"]) == ["dev:r1:", "sys:"])
        # THE assertion: shipping values down this socket gives the browser a second source for
        # what it also fetches, and the two can disagree.
        ok("it carries NOTHING else — no rows, no counts, no item payload",
           set(frame.keys()) == {"type", "topics"})
    finally:
        D.unsubscribe(q)


async def test_coalescing() -> None:
    print("coalescing — a burst of events becomes ONE refetch, not one per event")
    q = D.subscribe()
    try:
        # What a single build cycle actually looks like: run start, artifacts, report, phase advance.
        for kind in ("build.start", "artifact", "artifact", "run.report", "phase.advance"):
            D.publish_event({"context_id": "r1", "kind": kind})
        frame = await asyncio.wait_for(q.get(), timeout=2)
        ok("five events in a burst produced one frame", q.empty())
        ok("...carrying the union of their topics",
           sorted(frame["topics"]) == ["dev:r1:", "sys:"])

        # Distinct repos in one window merge rather than racing — the browser refetches both at once.
        D.publish_event({"context_id": "r1", "kind": "x"})
        D.publish_event({"context_id": "r2", "kind": "y"})
        frame = await asyncio.wait_for(q.get(), timeout=2)
        ok("two repos in one window ride out together",
           sorted(frame["topics"]) == ["dev:r1:", "dev:r2:", "sys:"])
    finally:
        D.unsubscribe(q)


def test_no_subscribers_is_free() -> None:
    print("nobody watching — the channel costs an unattended run nothing")
    assert not D.has_subscribers()
    before = set(D._pending)
    D.publish_event({"context_id": "r1", "kind": "build.start"})
    ok("publish with no panel open does not even queue a topic", set(D._pending) == before)
    # The common case IS unattended, and the push channel must add no work to that path.


async def test_observer_seam(tmp: Path) -> None:
    print("the seam — log_event notifies, and a broken observer cannot break the write")
    seen: list[dict] = []
    DS.subscribe_events(seen.append)

    boom_calls = {"n": 0}

    def boom(_row: dict) -> None:
        boom_calls["n"] += 1
        raise RuntimeError("observer exploded")

    DS.subscribe_events(boom)
    try:
        store = DS.DevStore(tmp / "dev.db")
        row = store.log_event("r1", "phase.advance", "moved to build", item_id="i1")
        ok("the write returned its row", row.get("kind") == "phase.advance")
        ok("the observer saw the event", len(seen) == 1 and seen[0]["context_id"] == "r1")
        ok("...with the fields topics_for needs", "context_id" in seen[0])
        ok("a raising observer was called", boom_calls["n"] == 1)
        # Notification is a side-channel: losing an invalidation costs one poll, losing the event
        # costs the durable trail.
        rows = store.list_events("r1")
        ok("...and the event is still durably logged", len(rows) == 1)
    finally:
        DS._event_observers.remove(seen.append) if seen.append in DS._event_observers else None
        DS._event_observers[:] = [f for f in DS._event_observers if f is not boom]


def test_backstop_contract() -> None:
    """The FE half's rule, asserted against the source so the two halves can't drift apart."""
    print("the FE backstop — push raises the poll interval, losing push releases it")
    store_src = src("web/frontend/src/lib/live/store.ts")
    ok("a backstop exists and is slow", "BACKSTOP_MS = 30_000" in store_src)
    ok("it applies only to ordinary-cadence feeds",
       "pushOnline && ms >= BACKSTOP_FLOOR_MS ? BACKSTOP_MS : ms" in store_src)
    # A very fast subscriber watches something continuous that writes no event, so slowing it
    # would regress.
    ok("...leaving fast live-run feeds alone", "BACKSTOP_FLOOR_MS = 5000" in store_src)
    ok("losing the channel re-times every live feed on the spot",
       "entries.forEach((e) => { if (e.subs.size) schedule(e) })" in store_src)

    # The "gone quiet" threshold is DERIVED from the cadence in force; the two numbers live in
    # different files.
    ok("the store reports the slowest cadence in force", "slowestMs," in store_src)
    bar = src("web/frontend/src/features/shell/StatusBar.tsx")
    ok("...and the staleness threshold is derived from it, not a constant",
       "stats.slowestMs * 1.5" in bar and "staleFor > quietAfter" in bar)

    push = src("web/frontend/src/lib/live/push.ts")
    ok("the backstop engages on the daemon's hello, not merely on the socket opening",
       "frame.type === 'dashboard_hello'" in push and "setPushOnline(true)" in push)
    ok("a close releases it", "setPushOnline(false)" in push)
    ok("the client refetches on a topic rather than applying a payload",
       "invalidate(...frame.topics)" in push)


async def main() -> None:
    test_topics()
    await test_frame_carries_topics_only()
    await test_coalescing()
    test_no_subscribers_is_free()
    with TemporaryDirectory() as td:
        await test_observer_seam(Path(td))
    test_backstop_contract()
    print(f"\nALL GREEN — {PASS} checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
