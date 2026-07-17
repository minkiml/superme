"""WS-S7 gate test (unit half) — attention engine + drilldown feeds (PRD stage S7).

The S7 gate proper is a browser E2E (run 2026-07-16, green: badge orange on awaiting_human ·
gate answerable from the rendered brief · spawn pushed from the graph · stepper/progress/tokens
in the drilldown · closeout lands unread and clears on open). This script pins the mechanical
substrate so regressions stay cheap: bucket assignment strict-priority + exactly-one-bucket ·
badge = top tier only · seen-stamp semantics (clears unread, never bumps updated_at) · the
checkpoint feed · workgraph git decoration · terminal items ask nothing at a gate.

Run: PYTHONPATH=. python -m scripts.test_ws_s7
"""

import tempfile
from pathlib import Path

from superme_agent.core import attention as AT
from superme_agent.core import artifacts as A
from superme_agent.core import gate_briefs as GB
from superme_agent.core import workgraph as WG
from superme_agent.core.dev_knowledge import DevKnowledgeService

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def test_attention() -> None:
    print("attention engine (D10 strict priority)")
    items = [
        {"id": "a", "title": "A", "phase": "deliver", "status": "awaiting_human"},
        {"id": "b", "title": "B", "phase": "build", "status": "active"},
        {"id": "c", "title": "C", "phase": "close", "status": "done", "done_at": "2026-07-16",
         "outcome": "completed"},
        {"id": "d", "title": "D", "phase": "close", "status": "done", "done_at": "2026-07-16",
         "outcome": "completed", "seen_at": "2026-07-16T10:00:00"},
        {"id": "e", "title": "E", "phase": "plan", "status": "active"},          # quiet
        {"id": "f", "title": "F", "phase": "triage", "status": "awaiting_child"},  # silent tier
    ]
    r = AT.assign(items, {"b", "a"})
    ok("needs_you wins over running (a is both)", [x["id"] for x in r["buckets"]["needs_you"]] == ["a"])
    ok("running from live rows only", [x["id"] for x in r["buckets"]["running"]] == ["b"])
    ok("unread = terminal + unseen; seen stays quiet",
       [x["id"] for x in r["buckets"]["unread"]] == ["c"])
    all_ids = [x["id"] for t in r["buckets"].values() for x in t]
    ok("every item in AT MOST one bucket; quiet/awaiting_child claim nothing",
       sorted(all_ids) == ["a", "b", "c"])
    ok("badge = TOP tier only (orange, count of that tier alone)",
       r["badge"] == {"tier": "needs_you", "color": "orange", "count": 1})
    ok("needs_you row names its gate", r["buckets"]["needs_you"][0]["gate"] == "deliver"
       and "your decision" in r["buckets"]["needs_you"][0]["reason"])
    r2 = AT.assign([items[2]], set())
    ok("unread badge is blue", r2["badge"]["color"] == "blue")
    ok("empty state → no badge", AT.assign([items[4]], set())["badge"] is None)


def test_seen_stamp(tmp: Path) -> None:
    print("seen stamp (read receipt)")
    dev = DevKnowledgeService()
    root = tmp / "seen-root"
    iid = dev.create_work_item(root, "readable")["id"]
    before = dev.read_work_item(root, iid)
    ok("stamps seen_at", dev.set_work_item_seen(root, iid) is True
       and dev.read_work_item(root, iid).get("seen_at") is not None)
    after = dev.read_work_item(root, iid)
    ok("a read receipt never bumps updated_at", after["updated_at"] == before["updated_at"])
    ok("id-like fields stay strings on single-item read",
       isinstance(after["root_id"], str))  # the all-digit-id 500, fixed 2026-07-16
    dev.set_work_item_terminal(root, iid, "completed")
    it = dev.read_work_item(root, iid)
    ok("terminal + seen = quiet (not unread)",
       AT.assign([it], set())["buckets"]["unread"] == [])


def test_terminal_brief(tmp: Path) -> None:
    print("terminal items ask nothing")
    dev = DevKnowledgeService()
    root = tmp / "tb-root"
    iid = dev.create_work_item(root, "done deal")["id"]
    dev.set_work_item_terminal(root, iid, "abandoned")
    it = dev.read_work_item(root, iid)
    b = GB.render_gate_brief(it, root / "work-items" / iid, root, None)
    ok("terminal → at_gate False + terminal note",
       not b["at_gate"] and "nothing left to decide" in b["brief"])


def test_checkpoint_feed(tmp: Path) -> None:
    print("checkpoint feed (drilldown continuity)")
    item_dir = tmp / "cf-item"
    item_dir.mkdir()
    ok("no dir → empty feed", A.checkpoint_feed(item_dir) == [])
    A.write_checkpoint(item_dir, None, working_on="first thing", decisions="d", remaining="r")
    A.write_checkpoint(item_dir, None, working_on="second thing", decisions="d", remaining="r")
    feed = A.checkpoint_feed(item_dir)
    ok("newest first, headline = first content line",
       len(feed) == 2 and feed[0]["headline"] == "second thing"
       and feed[1]["headline"] == "first thing")
    ok("git line carried", feed[0]["git"] == "(no git state)")


def test_graph_decoration() -> None:
    print("workgraph git decoration")
    g = WG.build(repo_id="r", items=[
        {"id": "i1", "kind": "implementation", "phase": "deliver", "status": "active",
         "git_branch": "item/i1-x", "git_merge_commit": "abc123"},
        {"id": "i2", "kind": "research", "phase": "plan", "status": "active"},
    ], deliverables=[], waves=[], inbox_rows=[])
    n1 = g.nodes[WG.item_node_id("i1")]
    n2 = g.nodes[WG.item_node_id("i2")]
    ok("impl node carries branch + merged flag",
       n1["git_branch"] == "item/i1-x" and n1["git_merged"] is True)
    ok("undecorated node stays honest", n2["git_branch"] is None and n2["git_merged"] is False)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_attention()
        test_seen_stamp(tmp)
        test_terminal_brief(tmp)
        test_checkpoint_feed(tmp)
        test_graph_decoration()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
