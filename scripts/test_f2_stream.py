"""F2 live timeline — Stage 1: the item_stream broker + capture_event publish gate.

Offline, no daemon. Proves the in-process pub/sub that lets build/vet turns stream into a watching
panel: item-scoped delivery, the has_subscribers gate (headless = no framing work), the capture-path
frame shape, and unsubscribe key cleanup. Run: PYTHONPATH=. python -m scripts.test_f2_stream
"""

import asyncio

from superme_agent.daemon.services import item_stream as S
from superme_agent.daemon.services import runs

PASS = 0


def ok(msg: str) -> None:
    global PASS
    PASS += 1
    print(f"  ok  {msg}")


async def main() -> None:
    q = S.subscribe("itemX")
    assert S.has_subscribers("itemX")
    S.publish("itemX", {"type": "timeline", "kind": "reply", "description": "hi"})
    f = await asyncio.wait_for(q.get(), timeout=1)
    assert f["kind"] == "reply" and f["description"] == "hi", f
    ok("subscribe → publish delivers the frame")

    S.publish("itemY", {"x": 1})
    assert q.empty(), "itemX queue received an itemY frame (cross-talk)"
    ok("frames are item-scoped (no cross-talk)")

    runs._publish_timeline("nobody", 5, "reply", "reply", "text", None, None)  # must not raise
    assert not S.has_subscribers("nobody")
    ok("capture publish is a no-op when nobody is watching")

    runs._publish_timeline("itemX", 7, "status", "Bash", "ls -la", "tool_1", None)
    f2 = await asyncio.wait_for(q.get(), timeout=1)
    assert f2["run_id"] == 7 and f2["tool_id"] == "tool_1" and f2["name"] == "Bash", f2
    assert f2["type"] == "timeline" and f2["item_id"] == "itemX", f2
    assert f2["parent_tool_id"] is None, f2
    ok("capture-path frame carries type/item_id/run_id/kind/name/tool_id")

    # A call made INSIDE a sub-agent rides the same channel, carrying WHOSE call it was — the live
    # panel needs it for the same reason the stored trail does (a fan-out interleaves its children).
    runs._publish_timeline("itemX", 7, "tool", "Read", "a.py", "tool_2", "spawn_1")
    f3 = await asyncio.wait_for(q.get(), timeout=1)
    assert f3["parent_tool_id"] == "spawn_1" and f3["tool_id"] == "tool_2", f3
    ok("a sub-agent's live frame names the spawn it came from")

    # A second panel on the same item both receive; one slow panel never blocks the other.
    q2 = S.subscribe("itemX")
    runs._publish_timeline("itemX", 8, "reply", "reply", "second", None, None)
    a = await asyncio.wait_for(q.get(), timeout=1)
    b = await asyncio.wait_for(q2.get(), timeout=1)
    assert a["description"] == b["description"] == "second"
    ok("two panels on one item both receive live frames")

    S.unsubscribe("itemX", q)
    S.unsubscribe("itemX", q2)
    assert not S.has_subscribers("itemX")
    ok("unsubscribe drops the item key (no leak)")


if __name__ == "__main__":
    asyncio.run(main())
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")
