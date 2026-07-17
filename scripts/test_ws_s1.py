"""WS-S1 gate test — contract & kernel state machine (workspace-workflow PRD stage S1).

Pure module-level unit tests (no daemon): KIND_PROFILES loud-fail + sequencing, work-item
create/read round-trip of the new contract fields, terminal setter rules, the typed-awaiting
status router (parent resumes exactly when the LAST blocking sibling closes), children-terminal
scan, inbox spawned_from column round-trip, and glance buckets. Self-cleaning (tempdirs).

Run: PYTHONPATH=. python -m scripts.test_ws_s1
"""

import tempfile
from pathlib import Path

from superme_agent.core import kind_profiles as kp
from superme_agent.core import status_router as sr
from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.core.dev_store import DevStore

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def test_kind_profiles() -> None:
    print("KIND_PROFILES")
    ok("known kinds", set(kp.KIND_PROFILES) == {"implementation", "research"})
    try:
        kp.get_profile("bogus")
        ok("unknown kind fails loud", False)
    except KeyError as e:
        ok("unknown kind fails loud", "bogus" in str(e))
    ok("null kind reads as implementation", kp.get_profile(None).kind == "implementation")
    # Full pipeline sequencing, both kinds.
    seq = []
    p = "triage"
    while p:
        seq.append(p)
        p = kp.next_phase("implementation", p)
    ok("impl pipeline", seq == ["triage", "plan", "build", "validate", "deliver", "close"], str(seq))
    seq, p = [], "triage"
    while p:
        seq.append(p)
        p = kp.next_phase("research", p)
    ok("research pipeline", seq == ["triage", "plan", "investigate", "report", "close"], str(seq))
    try:
        kp.next_phase("research", "build")
        ok("research can't sit in build", False)
    except KeyError:
        ok("research can't sit in build", True)
    ok("final phase detection", kp.is_final_phase("implementation", "close")
       and not kp.is_final_phase("implementation", "deliver"))
    ok("profiles disagree on machinery",
       kp.get_profile("implementation").worktree and not kp.get_profile("research").worktree
       and kp.get_profile("implementation").knowledge_writes
       and not kp.get_profile("research").knowledge_writes)
    ok("required artifacts declared",
       kp.get_profile("implementation").required_artifacts == ("plan", "validation", "readiness", "closeout")
       and kp.get_profile("research").required_artifacts == ("plan", "findings", "closeout"))


def test_item_contract(dev: DevKnowledgeService, root: Path) -> dict:
    print("work-item contract round-trip")
    try:
        dev.create_work_item(root, "bad", kind="bogus")
        ok("create rejects unknown kind", False)
    except KeyError:
        ok("create rejects unknown kind", True)
    ok("no folder from rejected create", not any((root / "work-items").glob("*")) if (root / "work-items").exists() else True)
    try:
        dev.create_work_item(root, "bad", spawned_from={"item": "x", "relation": "nope"})
        ok("create rejects bad relation", False)
    except ValueError:
        ok("create rejects bad relation", True)

    parent = dev.create_work_item(root, "parent item", "body text", inbox_id=7)
    it = dev.read_work_item(root, parent["id"])
    ok("enters at triage/active", it["phase"] == "triage" and it["status"] == "active")
    ok("kind stamped", it["kind"] == "implementation")
    ok("inbox_id stamped", it["inbox_id"] == 7)
    ok("no spawned_from on originals", it.get("spawned_from") is None)

    child = dev.create_work_item(
        root, "research child", kind="research",
        spawned_from={"item": parent["id"], "relation": "blocking", "note": "needs an answer"})
    c = dev.read_work_item(root, child["id"])
    ok("research kind round-trips", c["kind"] == "research")
    ok("spawned_from round-trips",
       c["spawned_from"] == {"item": parent["id"], "relation": "blocking", "note": "needs an answer"},
       str(c.get("spawned_from")))
    return {"parent": parent["id"], "child": child["id"]}


def test_terminal(dev: DevKnowledgeService, root: Path) -> None:
    print("terminal setter")
    wid = dev.create_work_item(root, "to close")["id"]
    try:
        dev.set_work_item_terminal(root, wid, "superseded")
        ok("superseded needs pointer", False)
    except ValueError:
        ok("superseded needs pointer", True)
    try:
        dev.set_work_item_terminal(root, wid, "exploded")
        ok("unknown outcome rejected", False)
    except ValueError:
        ok("unknown outcome rejected", True)
    ok("terminal write", dev.set_work_item_terminal(root, wid, "completed"))
    it = dev.read_work_item(root, wid)
    ok("terminal shape", it["status"] == "done" and it["outcome"] == "completed" and it["done_at"])
    ok("terminal idempotent", dev.set_work_item_terminal(root, wid, "completed") is False)
    wid2 = dev.create_work_item(root, "superseded one")["id"]
    dev.set_work_item_terminal(root, wid2, "superseded", superseded_by=wid)
    it2 = dev.read_work_item(root, wid2)
    ok("superseded_by stamped", it2["outcome"] == "superseded" and it2["superseded_by"] == wid)


def test_status_router() -> None:
    print("status router (typed awaiting)")
    def item(iid, status="active", sf=None):
        return {"id": iid, "status": status, "spawned_from": sf}
    edge = lambda p, rel: {"item": p, "relation": rel}

    parent = item("P", status="awaiting_child")
    c1 = item("C1", sf=edge("P", "blocking"))
    c2 = item("C2", sf=edge("P", "blocking"))
    c3 = item("C3", sf=edge("P", "parallel"))
    sp = item("S", sf=edge("P", "spawn"))
    items = [parent, c1, c2, c3, sp]

    all_done, opens = sr.children_terminal(items, "P")
    ok("open children detected", not all_done and set(opens) == {"C1", "C2", "C3"}, str(opens))
    ok("spawns are not children", "S" not in opens)

    # First blocking child closes → sibling C2 still open → NO resume.
    c1["status"] = "done"
    ok("no resume while a blocking sibling is open", sr.parent_to_resume(items, c1) is None)
    # Last blocking child closes → resume, even with the PARALLEL child still open.
    c2["status"] = "done"
    ok("resume on last blocking close", sr.parent_to_resume(items, c2) == "P")
    # Parallel child close never resumes; spawn close never resumes.
    c3["status"] = "done"
    ok("parallel close never resumes", sr.parent_to_resume(items, c3) is None)
    sp["status"] = "done"
    ok("spawn close never resumes", sr.parent_to_resume(items, sp) is None)
    # Parent not awaiting_child → no resume even when the last blocking child closes.
    parent["status"] = "active"
    ok("no resume unless awaiting_child", sr.parent_to_resume(items, c2) is None)
    # But parallel children STILL gate completion.
    c3["status"] = "active"
    all_done, opens = sr.children_terminal(items, "P")
    ok("parallel children gate completion", not all_done and opens == ["C3"], str(opens))


def test_inbox_spawned_from(tmp: Path) -> None:
    print("inbox spawned_from")
    store = DevStore(tmp / "dev.db")
    try:
        store.add_inbox("global", "x", spawned_from={"item": "abc", "relation": "nope"})
        ok("inbox rejects bad relation", False)
    except ValueError:
        ok("inbox rejects bad relation", True)
    row = store.add_inbox("global", "branch-off text", title="branch-off",
                          origin="agent", spawned_from={"item": "abc", "relation": "spawn"})
    ok("inbox spawned_from round-trips",
       row["spawned_from"] == {"item": "abc", "relation": "spawn"}, str(row.get("spawned_from")))
    plain = store.add_inbox("global", "plain capture")
    ok("plain capture has none", plain["spawned_from"] is None)
    listed = {r["id"]: r for r in store.list_inbox("global")}
    ok("list parses spawned_from", listed[row["id"]]["spawned_from"]["item"] == "abc")


def test_glance(dev: DevKnowledgeService, root: Path) -> None:
    print("glance buckets")
    wid = dev.create_work_item(root, "paging item")["id"]
    dev.set_work_item_status(root, wid, "awaiting_human")
    data = dev.read_all(root)
    g = data["glance"]
    ok("awaiting_human bucket", any(x["id"] == wid for x in g["awaiting_human"]))
    ok("by_status counts", g["by_status"].get("awaiting_human", 0) >= 1)
    ok("glance shape", set(g) >= {"by_status", "by_phase", "active", "awaiting_human"})
    # Dependency has ONE expression: a `blocking` branch-off pausing the parent at
    # awaiting_child. No dependency-id list, so no `blocked` bucket.
    ok("no blocked vocabulary", "blocked" not in g and "blocked_by" not in data["work_items"][0])


def main() -> None:
    dev = DevKnowledgeService()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = tmp / "dev"
        test_kind_profiles()
        test_item_contract(dev, root)
        test_terminal(dev, root)
        test_status_router()
        test_inbox_spawned_from(tmp)
        test_glance(dev, root)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
