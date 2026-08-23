"""The attention engine and the drilldown feeds.

An item lands in exactly one bucket by strict priority, and the badge shows the top tier only. A
seen-stamp clears unread without ever bumping `updated_at`.

Run: PYTHONPATH=. python -m scripts.test_ws_s7
"""

import tempfile
from pathlib import Path

from superme_agent.core import attention as AT
from superme_agent.core import artifacts as A
from superme_agent.core import gate_briefs as GB
from superme_agent.core import workgraph as WG
from superme_agent.core.dev_knowledge import DevKnowledgeService
from scripts.sources import src

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def test_attention() -> None:
    print("attention engine (D10 strict priority)")
    items = [
        {"id": "a", "title": "A", "phase": "review", "status": "awaiting_human"},
        {"id": "b", "title": "B", "phase": "build", "status": "active"},
        {"id": "c", "title": "C", "phase": "close", "status": "done", "done_at": "2026-07-16",
         "outcome": "completed"},
        {"id": "d", "title": "D", "phase": "close", "status": "done", "done_at": "2026-07-16",
         "outcome": "completed", "seen_at": "2026-07-16T10:00:00"},
        {"id": "e", "title": "E", "phase": "plan", "status": "active"},  # STALLED: gate, no run
        {"id": "f", "title": "F", "phase": "triage", "status": "awaiting_child"},  # silent tier
    ]
    r = AT.assign(items, {"b", "a"})
    ok("needs_you wins over running (a is both)", r["buckets"]["needs_you"][0]["id"] == "a")
    ok("running from live rows only", [x["id"] for x in r["buckets"]["running"]] == ["b"])
    ok("unread = terminal + unseen; seen stays quiet",
       [x["id"] for x in r["buckets"]["unread"]] == ["c"])
    all_ids = [x["id"] for t in r["buckets"].values() for x in t]
    ok("every item in AT MOST one bucket; awaiting_child claims nothing",
       sorted(all_ids) == ["a", "b", "c", "e"])
    ok("badge = TOP tier only (orange, count of that tier alone)",
       r["badge"] == {"tier": "needs_you", "color": "orange", "count": 2})
    ok("needs_you row names its gate", r["buckets"]["needs_you"][0]["gate"] == "review"
       and "your decision" in r["buckets"]["needs_you"][0]["reason"])
    r2 = AT.assign([items[2]], set())
    ok("unread badge is blue", r2["badge"]["color"] == "blue")
    ok("empty state → no badge", AT.assign([items[1]], set())["badge"] is None)

    # `active` at a gate with NO live run is a stall. One rule, computed here, read everywhere.
    stalled = next(x for x in r["buckets"]["needs_you"] if x["id"] == "e")
    ok("active at a gate with no run pages as needs_you", stalled["bucket"] == "needs_you")
    ok("...and says it is STALLED, not a normal gate pause",
       "stalled" in stalled["reason"] and "nothing running" in stalled["reason"])
    ok("...while a normal hold keeps its own wording",
       "stalled" not in r["buckets"]["needs_you"][0]["reason"])
    ok("active OFF a gate phase stays quiet (build/vet are mid-flight, not parked)",
       AT.assign([items[1]], set())["buckets"]["needs_you"] == [])
    ok("a gate phase WITH a live run is running, never a stall",
       AT.assign([items[4]], {"e"})["buckets"]["running"][0]["id"] == "e")
    ok("...and a deputy on the gate still wins over the stall rule",
       AT.assign([items[4]], {"e"}, {"e"})["buckets"]["deputy_working"][0]["id"] == "e")
    ok("a TERMINAL item at close never reads as stalled",
       AT.assign([items[3]], set())["buckets"]["needs_you"] == [])


def test_orphan_reconcile(tmp: Path) -> None:
    """The startup reconciler healed the RUN row and left the ITEM.

    The log stayed honest while the item kept `active` with no run and nothing to start one."""
    print("orphan reconcile (D3 — heal the item, not just the run)")
    from superme_agent.core.spine import SystemSpine
    sp = SystemSpine(db_path=tmp / "s.db", system_config=tmp / "sys.yaml",
                     repos_config=tmp / "repos.yaml")
    live = sp.start_item_run("demo-repo", feature="close", item_id="abc123", phase="close")
    done = sp.start_run("demo-repo", feature="chat")
    sp.finish_run(done, status="done")

    orphans = sp.reconcile()
    ok("reports the orphan it aborted, not just a count",
       [o["run_id"] for o in orphans] == [live])
    ok("...carrying what the caller needs to find the item",
       orphans[0]["repo_id"] == "demo-repo" and orphans[0]["item_id"] == "abc123"
       and orphans[0]["phase"] == "close")
    ok("...and len() still answers the old question", len(orphans) == 1)
    ok("an already-ended run is untouched",
       all(o["run_id"] != done for o in orphans))
    ok("second pass is a no-op — nothing left running", sp.reconcile() == [])

    # The daemon half: the rows above are only useful if something parks the items.
    ls = src("superme_agent/daemon/lifespan.py")
    ok("lifespan captures the orphans and parks them",
       "_orphans = app_state.spine.reconcile()" in ls
       and "_reconcile_orphaned_items(_orphans)" in ls)
    # Parking claims a DECISION is wanted, so a restart-stopped build looked like one. Orphans are
    # labelled `error`.
    ok("...to `error`, carrying what stopped them",
       'set_work_item_error(' in ls and "a daemon restart stopped the" in ls)
    ok("...and the phase ones are auto-resumed through the shared service",
       "from .services.resume import resume_item" in ls and "auto-resumed" in ls)
    ok("...skipping terminal items (idempotent)",
       'str(it.get("status")) == "done"' in ls)
    # Housekeeping is never fatal: raising outside the guard took daemon STARTUP down, which is
    # worse than the stranded item.
    ok("...and the whole body is guarded, so housekeeping can't stop the daemon booting",
       'log.exception("orphan reconciliation failed (non-fatal)")' in ls)
    ok("...after the close reconcile, so a mid-close item is finished there first",
       ls.index("_reconcile_close_steps()") < ls.index("_reconcile_orphaned_items(_orphans)"))
    ok("...and a dead `write` run's proposal is freed in the same pass (R3)",
       "_reconcile_stranded_proposals()" in ls)
    ok("...and leaves a trace rather than silently rewriting state",
       '"run.orphaned"' in ls)


def test_seen_stamp(tmp: Path) -> None:
    print("seen stamp (read receipt)")
    dev = DevKnowledgeService()
    root = tmp / "seen-root"
    iid = dev.create_work_item(root, "readable", kind="implementation")["id"]
    before = dev.read_work_item(root, iid)
    ok("stamps seen_at", dev.set_work_item_seen(root, iid) is True
       and dev.read_work_item(root, iid).get("seen_at") is not None)
    after = dev.read_work_item(root, iid)
    ok("a read receipt never bumps updated_at", after["updated_at"] == before["updated_at"])
    ok("id-like fields stay strings on single-item read",
       isinstance(after["root_id"], str))  # the all-digit-id 500
    dev.set_work_item_terminal(root, iid, "completed")
    it = dev.read_work_item(root, iid)
    ok("terminal + seen = quiet (not unread)",
       AT.assign([it], set())["buckets"]["unread"] == [])


def test_terminal_brief(tmp: Path) -> None:
    print("terminal items ask nothing")
    dev = DevKnowledgeService()
    root = tmp / "tb-root"
    iid = dev.create_work_item(root, "done deal", kind="implementation")["id"]
    dev.set_work_item_terminal(root, iid, "abandoned")
    it = dev.read_work_item(root, iid)
    s = GB.gate_state(it, root / "work-items" / iid, root, None)
    # A terminal item asks nothing, and that is now a typed fact rather than a sentence to find.
    ok("terminal → at_gate False + terminal True", not s["at_gate"] and s["terminal"])


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
        {"id": "i1", "kind": "implementation", "phase": "review", "status": "active",
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
        test_orphan_reconcile(tmp)
        test_seen_stamp(tmp)
        test_terminal_brief(tmp)
        test_checkpoint_feed(tmp)
        test_graph_decoration()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
