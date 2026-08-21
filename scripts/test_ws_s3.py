"""WS-S3 gate test — inbox routing & WorkGraph (workspace-workflow PRD stage S3).

Covers the PRD gate: all three relation types spawn correctly (blocking/parallel AUTO-PUSH and
land the brief in `preliminary/`; spawn waits for the owner's push); the children-terminal scan
reports the parent unclosable; the blocking push pauses the parent; the graph builds correct
nodes/edges/topo; the cycle guard trips on a manufactured loop; the inbox row survives push as
trace. Exercises the REAL create_inbox_item tool factory (async) — no daemon needed. Self-cleaning.

Run: PYTHONPATH=. python -m scripts.test_ws_s3
"""

import asyncio
import json
import tempfile
from pathlib import Path

from superme_agent.core import artifacts as A
from superme_agent.core import inbox_flow, status_router, workgraph
from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.core.dev_store import DevStore
from superme_agent.harness.tools.dev_tools import _create_inbox_item, _push_inbox_item

PASS = 0
CTX = "global"


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def tool_result_text(res: dict) -> str:
    return json.dumps(res)


def test_workgraph() -> None:
    print("workgraph build")
    items = [
        {"id": "p1", "title": "parent", "kind": "implementation", "phase": "build",
         "status": "active", "deliverable": "d-core"},
        {"id": "c1", "title": "block child", "kind": "implementation", "phase": "triage",
         "status": "active", "spawned_from": {"item": "p1", "relation": "blocking"}},
        {"id": "c2", "title": "par child", "phase": "triage", "status": "active",
         "spawned_from": {"item": "p1", "relation": "parallel"}},
        {"id": "w1", "title": "wave item", "phase": "plan", "status": "active", "wave": "w-a"},
        {"id": "solo", "title": "ungrouped", "phase": "plan", "status": "active"},
        {"id": "old", "title": "superseded", "phase": "close", "status": "done",
         "outcome": "superseded", "superseded_by": "p1"},
    ]
    g = workgraph.build(
        repo_id="repoX", items=items,
        deliverables=[{"id": "d-core", "title": "Core"}, {"id": "d-ui", "title": "UI"}],
        waves=[{"id": "w-a", "deliverable": "d-ui"}],
        inbox_rows=[
            {"id": 7, "status": "open", "title": "spawn idea",
             "spawned_from": {"item": "p1", "relation": "spawn"}},
            {"id": 8, "status": "pushed", "spawned_from": {"item": "p1", "relation": "spawn"}},
            {"id": 9, "status": "open", "title": "plain capture", "spawned_from": None},
        ])
    kinds = {n["kind"] for n in g.nodes.values()}
    ok("node kinds", kinds == {"repo_root", "deliverable", "work_item", "inbox_spawn"})
    ok("pushed/plain inbox rows are not nodes",
       "inbox:8" not in g.nodes and "inbox:9" not in g.nodes and "inbox:7" in g.nodes)
    def edges(kind):
        return [(e["src"], e["dst"]) for e in g.edges if e["kind"] == kind]
    ok("deliverable containment (direct + via wave)",
       ("deliverable:d-core", "item:p1") in edges("contains")
       and ("deliverable:d-ui", "item:w1") in edges("contains"), str(edges("contains")))
    ok("ungrouped original hangs off root", ("repo:repoX", "item:solo") in edges("contains"))
    ok("children don't hang off root", ("repo:repoX", "item:c1") not in edges("contains"))
    rel = {(e["src"], e["dst"]): e.get("relation") for e in g.edges if e["kind"] == "spawned_from"}
    ok("spawned_from edges typed",
       rel[("item:c1", "item:p1")] == "blocking" and rel[("item:c2", "item:p1")] == "parallel"
       and rel[("inbox:7", "item:p1")] == "spawn", str(rel))
    ok("supersedes edge", ("item:old", "item:p1") in edges("supersedes"))
    ok("ancestors/descendants",
       "item:p1" in g.descendants("item:c1") and "item:c1" in g.ancestors("item:p1"))
    ok("acyclic topo + no cycles", g.topo() is not None and g.cycles() == [])
    # Manufactured provenance loop → reported, never raised.
    items_loop = [
        {"id": "a", "phase": "triage", "status": "active",
         "spawned_from": {"item": "b", "relation": "spawn"}},
        {"id": "b", "phase": "triage", "status": "active",
         "spawned_from": {"item": "a", "relation": "spawn"}},
    ]
    g2 = workgraph.build(repo_id="r", items=items_loop, deliverables=[], waves=[], inbox_rows=[])
    ok("cycle guard trips", g2.topo() is None and g2.cycles()
       and set(g2.cycles()[0]) == {"item:a", "item:b"}, str(g2.cycles()))
    # Dangling spawned_from (deleted parent) tolerated.
    g3 = workgraph.build(repo_id="r", items=[
        {"id": "x", "phase": "triage", "status": "active",
         "spawned_from": {"item": "ghost", "relation": "spawn"}}],
        deliverables=[], waves=[], inbox_rows=[])
    ok("dangling parent tolerated", "item:x" in g3.nodes and g3.topo() is not None)


def test_brief(tmp: Path) -> None:
    print("handoff brief")
    folder = tmp / "inbox" / "1"
    p = Path(A.write_handoff_brief(folder, "T", background="the why", direction="option A vs B"))
    text = p.read_text()
    ok("provided sections filled", "the why" in text and "option A vs B" in text)
    ok("missing sections keep slots", "<fill:discussion>" in text and "<fill:constraints>" in text)
    issues = A.self_check(folder, "handoff-brief", path=p)
    ok("partial brief passes triage check (sections optional)", issues == [], str(issues))
    A.write_handoff_brief(folder, "T", discussion="round two")
    text = p.read_text()
    ok("append never rewrites", "the why" in text and "round two" in text and "---" in text)
    empty = tmp / "inbox" / "2"
    p2 = Path(A.write_handoff_brief(empty, "E"))
    issues = A.self_check(empty, "handoff-brief", path=p2)
    ok("all-empty brief fails", any("at least one" in i for i in issues), str(issues))


def test_push_flow(tmp: Path) -> None:
    print("push transaction (owner path)")
    store = DevStore(tmp / "dev.db")
    dev = DevKnowledgeService()
    root = tmp / "devroot"
    parent = dev.create_work_item(root, "parent", kind="implementation")["id"]

    row = store.add_inbox(CTX, "child work", title="child",
                          spawned_from={"item": parent, "relation": "blocking"})
    A.write_handoff_brief(inbox_flow.inbox_content_dir(root, row["id"]), "child",
                          background="ctx")
    wi = inbox_flow.push_inbox_item(store, dev, root, row, context_id=CTX)
    item = dev.read_work_item(root, wi["id"])
    ok("item carries provenance + inbox_id",
       item["spawned_from"]["relation"] == "blocking" and item["inbox_id"] == row["id"])
    prelim = root / "work-items" / wi["id"] / "preliminary" / "handoff-brief.md"
    ok("brief moved to preliminary/", prelim.exists() and "ctx" in prelim.read_text())
    ok("inbox folder cleared", not inbox_flow.inbox_content_dir(root, row["id"]).exists())
    fresh = store.get_inbox(row["id"])
    ok("row survives as trace", fresh["status"] == "pushed" and fresh["routed_to"] == wi["id"])
    ok("blocking push pauses parent",
       dev.read_work_item(root, parent)["status"] == "awaiting_child")
    ok("children-terminal scan blocks parent close",
       status_router.children_terminal(dev.read_all(root)["work_items"], parent)
       == (False, [wi["id"]]))
    try:
        inbox_flow.push_inbox_item(store, dev, root, fresh, context_id=CTX)
        ok("double push refused", False)
    except ValueError:
        ok("double push refused", True)
    # Bare row (manual FE capture, no folder) pushes fine with no move.
    bare = store.add_inbox(CTX, "bare capture", title="bare")
    wi2 = inbox_flow.push_inbox_item(store, dev, root, bare, context_id=CTX)
    ok("bare capture pushes without folder",
       not (root / "work-items" / wi2["id"] / "preliminary").exists())


def test_tool_autopush(tmp: Path) -> None:
    print("create_inbox_item tool (agent path)")
    store = DevStore(tmp / "tool.db")
    dev = DevKnowledgeService()
    root = tmp / "toolroot"
    parent = dev.create_work_item(root, "tool parent", kind="implementation")["id"]
    # The daemon injects the first-kick trigger; record what it's asked to triage (J: without it an
    # auto-pushed child lands at triage/active with no run and wedges its parent there forever).
    kicked: list[str] = []
    tool = _create_inbox_item(store=store, context_id=CTX, dev_root=root,
                              fire_triage=lambda cid: (kicked.append(cid), True)[1])

    async def call(args):
        return await tool(args)

    r = asyncio.run(call({"title": "bad", "body": "x", "relation": "blocking"}))
    ok("relation without parent rejected", "BOTH" in tool_result_text(r))
    # WHAT AN INBOX ITEM IS, enforced at the one boundary that mints them: a thing that becomes a
    # WORK ITEM when pushed. Caught live — a research ruling of "keep the file" was filed as an
    # implementation ticket whose own body read "no file change needed; this records the decision".
    # Checked AFTER the shape faults above: a malformed branch-off is the more basic mistake.
    r = asyncio.run(call({"title": "a decision, not work", "body": "the owner ruled: keep it"}))
    ok("a row that cannot name which machinery it becomes is refused",
       "not an inbox item" in tool_result_text(r))
    ok("…and the refusal says where such a thing DOES belong, so it is not merely blocked",
       "belongs in the record that holds it" in tool_result_text(r))
    # THE JOIN, not the file. Requiring the field at the tool while a skill still tells its agent to
    # omit it is a run that fails on its own instructions — each file correct, the join wrong. Every
    # skill that can reach `create_inbox_item` is checked against the boundary it calls.
    from pathlib import Path as _P
    _callers = [f for f in _P("superme_agent/harness/plugins").rglob("SKILL.md")
                if "create_inbox_item" in f.read_text()]
    ok("every skill that files an inbox item is found, so this check cannot pass by finding none",
       len(_callers) >= 5, f"found {len(_callers)}")
    for _f in _callers:
        _t = _f.read_text()
        ok(f"…{_f.parent.name} does not tell its agent to omit `work_kind`",
           "omit it" not in _t and "leaves the call to triage" not in _t
           and "left it for triage" not in _t)
        ok(f"…and {_f.parent.name} teaches no retired inbox kind",
           "kind: `todo`" not in _t and "`idea` for a proposal" not in _t)
    r = asyncio.run(call({"title": "bad", "body": "x", "spawned_from_item": "nope",
                          "relation": "blocking"}))
    ok("unknown parent rejected", "not found" in tool_result_text(r))

    r = asyncio.run(call({"title": "blocker", "body": "must fix first", "work_kind": "implementation",
                          "spawned_from_item": parent, "relation": "blocking",
                          "background": "hit a wall", "direction": "fix the wall"}))
    txt = tool_result_text(r)
    ok("blocking auto-pushed", "AUTO-PUSHED" in txt and "Parent paused" in txt, txt[:200])
    items = dev.read_all(root)["work_items"]
    child = next(it for it in items if it.get("spawned_from"))
    prelim = root / "work-items" / child["id"] / "preliminary" / "handoff-brief.md"
    ok("brief landed in child preliminary/", prelim.exists() and "hit a wall" in prelim.read_text())
    ok("parent awaiting_child", dev.read_work_item(root, parent)["status"] == "awaiting_child")
    ok("auto-pushed child gets the triage first-kick",
       kicked == [child["id"]] and "Triage is running on it." in txt, f"{kicked} / {txt[:120]}")

    r = asyncio.run(call({"title": "later idea", "body": "someday", "work_kind": "research",
                          "spawned_from_item": parent, "relation": "spawn",
                          "background": "came up while building"}))
    txt = tool_result_text(r)
    ok("spawn waits in inbox", "AUTO-PUSHED" not in txt and "owner" in txt, txt[:200])
    ok("a spawn is never kicked (it waits for the owner's push)", len(kicked) == 1, str(kicked))
    open_spawns = [x for x in store.list_inbox(CTX)
                   if x["status"] == "open" and x.get("spawned_from")]
    ok("spawn row open with provenance", len(open_spawns) == 1
       and open_spawns[0]["spawned_from"]["relation"] == "spawn")
    ok("spawn brief scaffolded",
       (inbox_flow.inbox_content_dir(root, open_spawns[0]["id"]) / "handoff-brief.md").exists())
    # And the owner's push then moves it (the FE-route path, same transaction).
    wi = inbox_flow.push_inbox_item(store, dev, root, open_spawns[0], context_id=CTX)
    ok("owner push moves spawn brief",
       (root / "work-items" / wi["id"] / "preliminary" / "handoff-brief.md").exists())
    ok("spawn never pauses parent",
       dev.read_work_item(root, parent)["status"] == "awaiting_child")  # unchanged from blocking


def test_push_tool(tmp: Path) -> None:
    """The general session's OPERATE act: start an item the owner points at. Pins the refusals
    (a phase session, an unknown id, a second push) as much as the happy path — an agent that can
    start work has to be unable to start the wrong work."""
    print("push_inbox_item tool (general-session path)")
    store = DevStore(tmp / "push.db")
    dev = DevKnowledgeService()
    root = tmp / "pushroot"
    kicked: list[str] = []

    def make(bound=None):
        return _push_inbox_item(store=store, context_id=CTX, dev_root=root, bound_item_id=bound,
                                fire_triage=lambda cid: (kicked.append(cid), True)[1])

    def call(tool, args):
        return tool_result_text(asyncio.run(tool(args)))

    row = store.add_inbox(CTX, "the token split dies on abort", title="fix aborted token split")
    A.write_handoff_brief(inbox_flow.inbox_content_dir(root, row["id"]),
                          "fix aborted token split", background="1.33M tokens unattributed")

    bound = make(bound="someitem0001")
    ok("a work-item session is refused", "not available inside a work-item session"
       in call(bound, {"item_id": row["id"]}))
    ok("a refused bound call pushes nothing", store.get_inbox(row["id"])["status"] == "open")

    tool = make()
    ok("unknown id refused", "read_inbox" in call(tool, {"item_id": 99999}))
    ok("non-numeric id refused", "numeric id" in call(tool, {"item_id": "twelve"}))
    ok("missing id refused", "numeric id" in call(tool, {}))

    txt = call(tool, {"item_id": row["id"]})
    ok("pushed → qualified ids in the result",
       f"inbox:{row['id']}" in txt and "item:" in txt, txt[:200])
    ok("first kick fired", len(kicked) == 1 and "Triage is running" in txt, f"{kicked} / {txt[:120]}")
    fresh = store.get_inbox(row["id"])
    ok("row survives as trace", fresh["status"] == "pushed" and fresh["routed_to"] == kicked[0])
    ok("brief moved into the item's preliminary/",
       (root / "work-items" / kicked[0] / "preliminary" / "handoff-brief.md").exists())

    ok("second push refused as trace", "already pushed" in call(tool, {"item_id": row["id"]}))
    ok("a refused second push mints nothing", len(kicked) == 1, str(kicked))

    other = store.add_inbox("some-other-repo", "not ours", title="foreign")
    ok("another project's row is invisible", "read_inbox" in call(tool, {"item_id": other["id"]}))


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_workgraph()
        test_brief(tmp)
        test_push_flow(tmp)
        test_tool_autopush(tmp)
        test_push_tool(tmp)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
