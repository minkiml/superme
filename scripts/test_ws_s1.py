"""The work-item contract and the kernel state machine, at module level.

Kind profiles fail loudly, terminal setters hold their rules, and a parent resumes exactly when
its LAST blocking sibling closes.

Run: PYTHONPATH=. python -m scripts.test_ws_s1
"""

import re
import tempfile
from pathlib import Path

from superme_agent.core.vocab import kind_profiles as kp
from superme_agent.core.vocab import status_router as sr
from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.core.dev_store import DevStore
from scripts.sources import src

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
    ok("impl pipeline", seq == ["triage", "plan", "build", "vet", "review", "close"], str(seq))
    seq, p = [], "triage"
    while p:
        seq.append(p)
        p = kp.next_phase("research", p)
    # The shared spine is triage, work, review, close. `plan` belongs to the kinds that VET.
    ok("research pipeline", seq == ["triage", "investigate", "review", "close"], str(seq))
    ok("spine phases are shared by every kind",
       all(set(("triage", "review", "close")) <= set(p.phases)
           for p in kp.KIND_PROFILES.values()))
    gsrc = src("superme_agent/daemon/services/gates.py")
    ok("the advance guard asks for plan.md only from a kind that HAS a plan phase",
       'if nxt in ("build", "investigate") and "plan" in profile.phases:' in gsrc)
    ok("plan belongs to the kinds that VET, and to no others",
       all(("plan" in p.phases) == ("vet" in p.phases) for p in kp.KIND_PROFILES.values()))
    try:
        kp.next_phase("research", "plan")
        ok("research can't sit in plan", False)
    except KeyError:
        ok("research can't sit in plan", True)
    ok("the retired report phase is gone", "report" not in kp.ALL_PHASES)
    try:
        kp.next_phase("research", "build")
        ok("research can't sit in build", False)
    except KeyError:
        ok("research can't sit in build", True)
    ok("final phase detection", kp.is_final_phase("implementation", "close")
       and not kp.is_final_phase("implementation", "review"))
    ok("profiles disagree on machinery",
       kp.get_profile("implementation").worktree and not kp.get_profile("research").worktree
       and kp.get_profile("implementation").knowledge_writes
       and not kp.get_profile("research").knowledge_writes)
    ok("required artifacts declared (readiness and closeout are gone)",
       kp.get_profile("implementation").required_artifacts == ("plan", "review")
       and kp.get_profile("research").required_artifacts
       == ("investigation", "review"))
    ok("no kind requires an artifact from a phase it does not have",
       all(not ({"plan": "plan", "investigation": "investigate", "review": "review"}.get(a)
                and {"plan": "plan", "investigation": "investigate",
                     "review": "review"}[a] not in p.phases)
           for p in kp.KIND_PROFILES.values() for a in p.required_artifacts))


def test_item_contract(dev: DevKnowledgeService, root: Path) -> dict:
    print("work-item contract round-trip")
    try:
        dev.create_work_item(root, "bad", kind="bogus")
        ok("create rejects unknown kind", False)
    except KeyError:
        ok("create rejects unknown kind", True)
    ok("no folder from rejected create", not any((root / "work-items").glob("*")) if (root / "work-items").exists() else True)
    try:
        dev.create_work_item(root, "bad", spawned_from={"item": "x", "relation": "nope"}, kind="implementation")
        ok("create rejects bad relation", False)
    except ValueError:
        ok("create rejects bad relation", True)

    parent = dev.create_work_item(root, "parent item", "body text", inbox_id=7, kind="implementation")
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
    wid = dev.create_work_item(root, "to close", kind="implementation")["id"]
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
    wid2 = dev.create_work_item(root, "superseded one", kind="implementation")["id"]
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
    # Only `blocking` holds a parent mid-pipeline, which is the whole meaning of `parallel`.
    c2["status"] = "done"
    ok("resume on last blocking close", sr.parent_to_resume(items, c2) == "P")
    sp["status"] = "done"
    ok("spawn close never resumes", sr.parent_to_resume(items, sp) is None)
    # Parent not awaiting_child → no resume even when the last blocking child closes.
    parent["status"] = "active"
    ok("no resume unless awaiting_child", sr.parent_to_resume(items, c2) is None)
    # But parallel children STILL gate completion.
    c3["status"] = "active"
    all_done, opens = sr.children_terminal(items, "P")
    ok("parallel children gate completion", not all_done and opens == ["C3"], str(opens))

    # --- the close-phase asymmetry --------------------------------------
    # A parent at close with an open PARALLEL child had no releaser.
    par = {"id": "Q", "status": "awaiting_child", "phase": "close", "kind": "implementation"}
    pk = {"id": "QC", "status": "active", "spawned_from": {"item": "Q", "relation": "parallel"}}
    fam = [par, pk]
    ok("a parallel child HOLDS a parent at close",
       sr.children_terminal(fam, "Q") == (False, ["QC"]))
    pk.update(status="done", done_at="now")
    ok("and releases it when it lands", sr.parent_to_resume(fam, pk) == "Q")
    # …but only when nothing else holds it.
    blk = {"id": "QB", "status": "active", "spawned_from": {"item": "Q", "relation": "blocking"}}
    ok("not while a blocking sibling is still open",
       sr.parent_to_resume([par, pk, blk], pk) is None)
    ok("the close criterion is phase-independent",
       all(sr.children_terminal([{**par, "phase": p}, blk], "Q") == (False, ["QB"])
           for p in ("build", "vet", "review", "close")))


def test_peer_sequencing() -> None:
    print("peer sequencing (after: edges)")
    def item(iid, status="active", after=None, outcome=None):
        d = {"id": iid, "status": status}
        if after is not None:
            d["after"] = after
        if outcome is not None:
            d["outcome"] = outcome
        return d

    a, b = item("A"), item("B")
    c = item("C", status="awaiting_upstream", after=["A", "B"])
    d = item("D", status="awaiting_upstream", after=["A"])
    items = [a, b, c, d]

    ok("no after → no constraint", sr.upstream_ids(a) == [])
    ok("both upstreams open", sr.upstream_state(items, c) == (["A", "B"], []))

    # A completes: D (only upstream) releases; C still waits on B.
    a.update(status="done", outcome="completed")
    rel, page = sr.items_to_release(items, "A")
    ok("release only when EVERY upstream is done", rel == ["D"] and not page, f"{rel} {page}")

    # B completes → C releases.
    b.update(status="done", outcome="completed")
    rel, page = sr.items_to_release(items, "B")
    ok("last upstream releases the item", rel == ["C"], str(rel))

    # An ABANDONED upstream never releases — it pages, because nothing will land.
    e = item("E", status="done", outcome="abandoned")
    f = item("F", status="awaiting_upstream", after=["E"])
    rel, page = sr.items_to_release([e, f], "E")
    ok("abandoned upstream pages, never releases", not rel and page == ["F"], f"{rel} {page}")
    ok("failed upstream reported separately", sr.upstream_state([e, f], f) == ([], ["E"]))

    # A MISSING upstream counts as satisfied: a deleted predecessor must not wedge its downstream
    # forever.
    g = item("G", status="awaiting_upstream", after=["GONE"])
    ok("missing upstream is satisfied, not a wedge",
       sr.items_to_release([g], "GONE") == (["G"], []))

    # Items not parked (or parked on someone else) are never touched by an unrelated close.
    h = item("H", status="active", after=["A"])
    ok("active item is not re-released", sr.items_to_release([a, h], "A") == ([], []))
    ok("unrelated parked item untouched", sr.items_to_release([a, c], "ZZZ") == ([], []))


def test_after_field(dev: DevKnowledgeService, root: Path) -> None:
    print("after field (creation contract)")
    up = dev.create_work_item(root, "upstream", kind="implementation")["id"]
    down = dev.create_work_item(root, "downstream", after=[up], kind="implementation")["id"]
    it = dev.read_work_item(root, down)
    ok("after persisted as string ids", it["after"] == [up], str(it.get("after")))
    ok("parks immediately when upstream is open", it["status"] == "awaiting_upstream", it["status"])
    ok("after survives the bulk read",
       [x for x in dev.read_all(root)["work_items"] if x["id"] == down][0]["after"] == [up])

    # A completed upstream means no wait at all — the item enters active like any other.
    dev.set_work_item_terminal(root, up, "completed")
    d2 = dev.create_work_item(root, "late arrival", after=[up], kind="implementation")["id"]
    ok("no park when upstream already done",
       dev.read_work_item(root, d2)["status"] == "active")

    # A typo'd id is refused at creation — the one failure this edge must never absorb quietly.
    try:
        dev.create_work_item(root, "bad edge", after=["nope"], kind="implementation")
        ok("unknown after id rejected", False, "no raise")
    except ValueError as e:
        ok("unknown after id rejected", "nope" in str(e))


def test_autopilot_decision() -> None:
    print("autopilot decision (core.autopilot)")
    from superme_agent.core import autopilot
    from superme_agent.core.vocab import kind_profiles
    nxt = kind_profiles.next_phase

    def item(phase="triage", status="awaiting_human", ap=True, done=False):
        d = {"kind": "implementation", "phase": phase, "status": status}
        if ap:
            d["autopilot"] = True
        if done:
            d["done_at"] = "2026-07-20"
        return d

    ok("off → None", autopilot.auto_advance_target(item(ap=False), nxt) is None)
    ok("triage gate → plan", autopilot.auto_advance_target(item("triage"), nxt) == "plan")
    ok("plan gate → build", autopilot.auto_advance_target(item("plan"), nxt) == "build")
    ok("review is the exclusion zone", autopilot.auto_advance_target(item("review"), nxt) is None)
    ok("close has no next → None", autopilot.auto_advance_target(item("close"), nxt) is None)
    ok("not at a gate (active) → None",
       autopilot.auto_advance_target(item("plan", status="active"), nxt) is None)
    ok("awaiting_upstream is not a gate → None",
       autopilot.auto_advance_target(item("triage", status="awaiting_upstream"), nxt) is None)
    ok("terminal → None", autopilot.auto_advance_target(item("plan", done=True), nxt) is None)


def test_autopilot_concurrency() -> None:
    print("autopilot concurrency (slice 3)")
    from superme_agent.core import autopilot

    def it(iid, phase="build", status="active", ap=True, upd="2026-07-20", done=False):
        d = {"id": iid, "phase": phase, "status": status, "updated_at": upd}
        if ap:
            d["autopilot"] = True
        if done:
            d["done_at"] = "2026-07-20"
        return d

    items = [
        it("b1", "build"), it("v1", "vet"),               # 2 autopilot slots occupied
        it("h1", "build", ap=False),                       # hand-driven — NOT counted
        it("p1", "plan"),                                  # not in a build slot
        it("d1", "build", done=True),                      # terminal — not counted
    ]
    ok("counts only autopilot build/vet", autopilot.occupied_build_slots(items) == 2)
    ok("hand-driven build never counted",
       autopilot.occupied_build_slots([it("h", "build", ap=False)]) == 0)
    ok("free @cap4 = 2", autopilot.free_build_slots(items, 4) == 2)
    ok("free @cap2 = 0 (full)", autopilot.free_build_slots(items, 2) == 0)
    ok("free never negative @cap1", autopilot.free_build_slots(items, 1) == 0)

    # The queue: awaiting_slot autopilot items, oldest-updated first.
    queued = [
        it("q_late", "plan", status="awaiting_slot", upd="2026-07-20T10:00"),
        it("q_early", "plan", status="awaiting_slot", upd="2026-07-20T09:00"),
        it("q_notap", "plan", status="awaiting_slot", ap=False, upd="2026-07-20T08:00"),
    ]
    held = autopilot.held_for_slot(queued)
    ok("queue is autopilot-only, FIFO by updated_at",
       [h["id"] for h in held] == ["q_early", "q_late"], str([h["id"] for h in held]))


def test_autopilot_field(dev: DevKnowledgeService, root: Path) -> None:
    print("autopilot field (creation + toggle)")
    plain = dev.create_work_item(root, "hand-driven", kind="implementation")["id"]
    ok("absent reads False", dev.read_work_item(root, plain)["autopilot"] is False)
    auto = dev.create_work_item(root, "enrolled", autopilot=True, kind="implementation")["id"]
    ok("born-on reads True", dev.read_work_item(root, auto)["autopilot"] is True)
    ok("survives bulk read",
       [x for x in dev.read_all(root)["work_items"] if x["id"] == auto][0]["autopilot"] is True)

    ok("toggle on", dev.set_work_item_autopilot(root, plain, True))
    ok("now on", dev.read_work_item(root, plain)["autopilot"] is True)
    ok("toggle on is idempotent", dev.set_work_item_autopilot(root, plain, True) is False)
    ok("toggle off", dev.set_work_item_autopilot(root, plain, False))
    ok("now off + line gone", dev.read_work_item(root, plain)["autopilot"] is False)
    ok("frontmatter carries no dead false",
       "autopilot:" not in (root / "work-items" / plain / "item.md").read_text(encoding="utf-8"))
    # Status must survive the autopilot rewrite (the regex threads around the status line).
    dev.set_work_item_autopilot(root, plain, True)
    ok("status intact after toggle", dev.read_work_item(root, plain)["status"] == "active")


def test_advance_review_status(dev: DevKnowledgeService, root: Path, tmp: Path) -> None:
    print("advance_item: review is a gate — rests awaiting_human, not active (B5/P6)")
    from types import SimpleNamespace
    from superme_agent.core.spine import SystemSpine
    from superme_agent.daemon.services import gates
    iid = dev.create_work_item(root, "to-review", autopilot=True, kind="implementation")["id"]
    dev.set_work_item_phase(root, iid, "vet")
    dev.set_work_item_status(root, iid, "active")
    ctx = SimpleNamespace(internal_root=tmp, cwd=tmp, id="r")
    spine = SystemSpine(db_path=tmp / "adv_spine.db")
    store = DevStore(tmp / "adv_dev.db")
    out = gates.advance_item(ctx, "r", iid, dev=dev, dev_store=store, spine=spine,
                             actor="owner")
    ok("advance vet→review returns the flip", out["phase"] == "review" and out["from"] == "vet")
    it = dev.read_work_item(root, iid)
    ok("review lands at awaiting_human (the paged gate), never active-with-no-run",
       it["phase"] == "review" and it["status"] == "awaiting_human")


def test_deputy(tmp: Path) -> None:
    print("deputy core (slice 4)")
    import asyncio
    from superme_agent.core import deputy as D, kernel_speech as KS
    from superme_agent.core.spine import SystemSpine
    from superme_agent.harness.tools import run_tools as RT
    # strictness vocabulary is ONE list, shared by the setting (spine) and the prompt (kernel).
    ok("strictness levels aligned",
       set(SystemSpine.DEPUTY_STRICTNESS_LEVELS) == set(KS.DEPUTY_STRICTNESS_LEVELS)
       == {"low", "medium", "high", "extra"})
    ok("strictness default aligned",
       SystemSpine.DEPUTY_STRICTNESS_DEFAULT == KS.DEPUTY_STRICTNESS_DEFAULT == "medium")

    # Valid decisions land in the sink; an invalid call errors back to the agent and falls to the
    # owner.
    sink: dict = {}
    call = RT._deputy_verdict(verdict_sink=sink)
    r = asyncio.run(call({"machine": {"decision": "approve", "gate": "plan"},
                          "user": {"checked": "read plan.md tasks", "because": "sound"}}))
    v = sink.get("verdict")
    ok("verdict approve lands", not r.get("is_error")
       and v and v["decision"] == "approve" and v["gate"] == "plan")
    ok("verdict bogus decision rejected", asyncio.run(call(
        {"machine": {"decision": "nope", "gate": "plan"},
         "user": {"checked": "c", "because": "b"}})).get("is_error") is True)
    ok("send_back without change rejected", asyncio.run(call(
        {"machine": {"decision": "send_back", "gate": "plan"},
         "user": {"checked": "c", "because": "b"}})).get("is_error") is True)
    ok("escalate without what_to_do rejected", asyncio.run(call(
        {"machine": {"decision": "escalate", "gate": "review"},
         "user": {"checked": "c", "because": "b",
                  "escalation": {"summary": "s", "concerns": ["c"]}}})).get("is_error") is True)
    ok("escalate without concerns rejected", asyncio.run(call(
        {"machine": {"decision": "escalate", "gate": "review"},
         "user": {"checked": "c", "because": "b",
                  "escalation": {"summary": "s", "what_to_do": ["go"]}}})).get("is_error") is True)
    asyncio.run(call({"machine": {"decision": "escalate", "gate": "review"},
                      "user": {"checked": "vet ledger", "because": "UX feel",
                               "escalation": {"summary": "Open /dash and judge the feel",
                                              "concerns": ["feel is not testable"],
                                              "what_to_do": ["Run export", "Expect a CSV"]}}}))
    v2 = sink["verdict"]
    esc = v2["escalation"]
    ok("escalation carries every part the deputy gave",
       v2["decision"] == "escalate" and "Run export" in esc and "CSV" in esc
       and "feel is not testable" in esc)
    # The KERNEL owns the layout, so it cannot drift between deputies.
    ok("escalation is assembled as the labelled markdown card",
       esc.startswith("**Issue summary:** Open /dash")
       and "**Concern:**" in esc and "**What to do:**" in esc
       and esc.count("\n- ") == 3)

    # Preamble: identity + the floor + the injected strictness band + the tool ending.
    p = KS.deputy_preamble("extra")
    ok("preamble carries identity+floor", "Deputy" in p and "must NOT approve" in p)
    ok("preamble injects the level", "extra" in p and "plumbing" in p)
    ok("preamble names the verdict tool", "deputy_verdict" in p)
    p_lo = KS.deputy_preamble("low")
    ok("strictness band varies by level", "maximum delegated autonomy" in p_lo.lower())
    ok("unknown strictness falls back to medium",
       KS.deputy_preamble("bogus") == KS.deputy_preamble("medium"))

    # The owner's report and the typed rows, never their decision: a judge handed a verdict judges
    # it.
    state = {"phase": "review", "checks": [
        {"criterion": "evidence_fresh", "ok": False, "detail": "c3 FAILED", "blocking": True},
        {"criterion": "git_fresh", "ok": True, "detail": "behind 0", "blocking": False}],
        "blocked_by": ["c3 FAILED"]}
    report = {"text": "REPORT-BODY", "contract": "artifacts/build-vet-3.md"}
    b = KS.deputy_brief_block("abc123def456", "T", "review", state=state, report=report,
                              mandate="MAND", log_digest="LOG", success_signal="SIG-VERBATIM",
                              verdicts=[{"check": "c3", "passed": False, "deferred": False,
                                         "cycle": 3, "how": "pytest", "result": "1 failed"}])
    ok("payload has mandate + log + the owner's report verbatim",
       "MAND" in b and "LOG" in b and "REPORT-BODY" in b)
    ok("...and the PATH to the full contract, so 'inspect with Read' is executable",
       "artifacts/build-vet-3.md" in b)
    ok("check rows carry the must-resolve mark the owner's Approve depends on",
       "evidence_fresh" in b and "must-resolve" in b and "greyed" in b)
    ok("review injects the verbatim success signal", "SIG-VERBATIM" in b)
    # The merge gate must read verdicts, not a count.
    ok("review sees the vet's actual per-check verdicts",
       "c3" in b and "FAIL" in b and "pytest" in b)
    ok("the owner's decision block is GONE — no recommendation is fed to the judge",
       "recommended" not in b.lower() and "Effort:" not in b)
    b2 = KS.deputy_brief_block("abc123def456", "T", "plan",
                               state={"phase": "plan", "checks": [], "blocked_by": []},
                               report=None, mandate="M", log_digest="L")
    ok("plan payload omits the signal + verdict sections", "success signal" not in b2.lower())
    ok("a phase with no report says so, and points at the contract instead",
       "no report-plan.md exists" in b2)

    # Mandate is per-repo, the decision log per-item, the digest item-and-gate scoped: no cross-
    # item precedent.
    d = tmp / "deputy-dev"
    ok("mandate seeds", "Deputy mandate" in D.read_mandate(d) and D.mandate_path(d).exists())
    itemA = tmp / "work-items" / "itemA"
    itemB = tmp / "work-items" / "itemB"
    D.append_decision(itemA, "plan", "approve", "plan sound")
    D.append_decision(itemA, "review", "send_back", "vet gap", change="cover empty input")
    D.append_decision(itemB, "plan", "escalate", "owner reserved")
    D.append_decision(itemA, "review", "send_back", "still short")
    ok("send-back count is per-item", D.count_send_backs(itemA) == 2
       and D.count_send_backs(itemB) == 0)
    ok("send-back count can scope to a gate",
       D.count_send_backs(itemA, "review") == 2 and D.count_send_backs(itemA, "plan") == 0)
    ok("send-back cap is 3", D.SEND_BACK_CAP == 3)
    ok("item log is item-scoped, ordered",
       [r["decision"] for r in D.item_decisions(itemA)]
       == ["approve", "send_back", "send_back"])
    ok("gate_decisions filters to one gate",
       [r["decision"] for r in D.gate_decisions(itemA, "review")] == ["send_back", "send_back"])
    digA_review = D.log_digest(itemA, "review")
    ok("digest is item+gate scoped (this gate's calls only, no cross-item)",
       "send_back" in digA_review and "cover empty input" in digA_review
       and "itemB" not in digA_review and "plan sound" not in digA_review)
    ok("digest empty when no calls at this gate", D.log_digest(itemA, "close") == "")
    try:
        D.append_decision(tmp / "work-items" / "x", "plan", "bogus", "y")
        ok("log rejects bad decision", False)
    except ValueError:
        ok("log rejects bad decision", True)

    # The gate map: deputy judges triage/plan/review, never build/vet/close.
    from superme_agent.daemon.services import deputy as dsvc
    ok("deputy gates = triage/plan/review",
       dsvc.deputy_gate_for({"phase": "plan"}) == "plan"
       and dsvc.deputy_gate_for({"phase": "review"}) == "review"
       and dsvc.deputy_gate_for({"phase": "build"}) is None
       and dsvc.deputy_gate_for({"phase": "close"}) is None)
    # ...and a phase the deputy does NOT judge is still advanced mechanically, or an autopilot
    # item strands with no self-driver.
    gsrc = src("superme_agent/daemon/services/gates.py")
    ok("a non-gate phase falls THROUGH the deputy branch to the mechanical advance",
       "if deputy_svc.deputy_gate_for(item) is not None:" in gsrc
       and "if deputy_svc.deputy_gate_for(item) is None:\n                return" not in gsrc)

    # Under cap a send-back FIRES a live turn rather than parking the owner; at cap it escalates.
    from superme_agent.daemon.services import runs as rsvc
    import types as _t
    class _Cap:
        def __init__(self): self.escalated = []; self.status = None
        def log_event(self, cid, kind, summary, **kw): self.escalated.append(kind)
        def set_work_item_status(self, root, iid, st): self.status = st
    cap = _Cap()
    fired = []
    saved = (dsvc._dev, dsvc._dev_store, rsvc.fire_deputy_feedback, D.count_send_backs)
    ctx = _t.SimpleNamespace(internal_root=(tmp / "ctx"))
    try:
        dsvc._dev = cap; dsvc._dev_store = cap
        verdict = {"decision": "send_back", "change": "handle empty input", "because": "gap"}
        # (a) under cap, plan send-back → live turn fired, no escalation
        rsvc.fire_deputy_feedback = lambda cid, iid, **kw: (fired.append((iid, kw.get("phase"))) or True)
        D.count_send_backs = lambda item_dir, gate=None: 0
        dsvc._do_send_back(ctx, "ctx", "itmX", "plan", verdict)
        ok("under cap: plan send-back fires a live turn, no escalate",
           fired == [("itmX", "plan")] and "deputy.escalate" not in cap.escalated)
        # (b) at cap → escalate, never fire
        fired.clear(); cap.escalated.clear()
        D.count_send_backs = lambda item_dir, gate=None: 3
        dsvc._do_send_back(ctx, "ctx", "itmX", "plan", verdict)
        ok("at cap: send-back becomes an escalation, no turn fired",
           fired == [] and "deputy.escalate" in cap.escalated)
        # (c) undeliverable (no session / race) → falls to the owner
        fired.clear(); cap.escalated.clear()
        D.count_send_backs = lambda item_dir, gate=None: 0
        rsvc.fire_deputy_feedback = lambda cid, iid, **kw: False
        dsvc._do_send_back(ctx, "ctx", "itmX", "triage", verdict)
        ok("undeliverable send-back escalates to the owner", "deputy.escalate" in cap.escalated)
        # A review send-back fires with a downstream digest, so the re-plan gets its context.
        fired.clear(); cap.escalated.clear()
        kw_seen = {}
        rsvc.fire_deputy_feedback = lambda cid, iid, **kw: (kw_seen.update(kw) or True)
        import superme_agent.daemon.services.git_ops as _go
        saved_dig = _go.build_downstream_digest
        _go.build_downstream_digest = lambda item_dir, **kw: "DIGEST-BODY"
        try:
            dsvc._do_send_back(ctx, "ctx", "itmX", "review", verdict)
        finally:
            _go.build_downstream_digest = saved_dig
        ok("review send-back fires phase='review' with a downstream digest",
           kw_seen.get("phase") == "review" and kw_seen.get("digest") == "DIGEST-BODY"
           and "deputy.escalate" not in cap.escalated)
    finally:
        dsvc._dev, dsvc._dev_store, rsvc.fire_deputy_feedback, D.count_send_backs = saved

    # digest builder: readiness + latest vet report → one context blob; empty item → None.
    from superme_agent.core import artifacts as _A
    from superme_agent.daemon.services import git_ops as GO
    idir = tmp / "wi-dig"; (idir / "artifacts").mkdir(parents=True)
    ok("empty item → no digest", GO.build_downstream_digest(idir) is None)
    # The digest reads review's AGENT-facing record: a re-plan needs the change inventory the
    # owner's prose never carries.
    (idir / "artifacts" / "review.md").write_text("## Change inventory\nbuilt the thing\n", encoding="utf-8")
    (idir / "artifacts" / "build-vet-1.md").write_text("## Verification\nedge case X fails\n", encoding="utf-8")
    dig = GO.build_downstream_digest(idir)
    ok("digest carries the review RECORD + cycle report",
       dig and "built the thing" in dig and "edge case X fails" in dig and "build-vet-1.md" in dig)

    # delta feed + forward-only lifetime + per-gate cap.
    di = tmp / "wi-slice3"
    ok("delta is None on a first judgment (no prior send-back at this gate)",
       dsvc._build_delta(di, "plan", {}) is None)
    D.append_decision(di, "plan", "send_back", "plan too thin", change="add a rollback step")
    delta = dsvc._build_delta(di, "plan", {"tasks_done": 2, "tasks_total": 5, "cycle": 1})
    ok("delta on re-entry carries the asked-for change + movement, as a pointer",
       delta and "add a rollback step" in delta and "tasks 2/5" in delta
       and "verify against the artifacts" in delta)
    ok("delta is gate-scoped (a plan send-back does not surface at review)",
       dsvc._build_delta(di, "review", {}) is None)
    # forward-only / flow-through signal: only a REVIEW send-back marks the review loop.
    ok("not in a review loop from a plan send-back", not dsvc._in_review_loop(di))
    D.append_decision(di, "review", "send_back", "delivery gap", change="cover the empty case")
    ok("in a review loop once review has sent back", dsvc._in_review_loop(di))
    # per-gate (per-episode) cap: review's count is independent of the earlier plan send-back.
    ok("send-back cap counts per gate",
       D.count_send_backs(di, "review") == 1 and D.count_send_backs(di, "plan") == 1
       and D.count_send_backs(di) == 2)


def test_itemize_launch(dev: DevKnowledgeService, root: Path) -> None:
    print("itemize launch (cohort — slice 4c)")
    items = [
        {"key": "d-cli", "title": "CLI foundation", "kind": "implementation"},
        {"key": "d-collect", "title": "Commit collection", "after": ["d-cli"],
         "kind": "implementation"},
        {"key": "d-classify", "title": "Branch classification", "after": ["d-cli", "d-collect"],
         "kind": "research"},
    ]
    try:
        dev.itemize_launch(root, [{"key": "d-x", "title": "Unkinded item"}])
        ok("a cohort item with no kind is refused", False)
    except ValueError:
        ok("a cohort item with no kind is refused — direct mint skips the inbox, not the choice",
           True)
    r = dev.itemize_launch(root, items)
    ok("the caller's kind is BOTH the kind and the proposal triage confirms",
       all((lambda it: it["kind"] == it["proposed_kind"])(
           dev.read_work_item(root, c["id"])) for c in r["created"]))
    ok("one cohort id minted", bool(r["cohort"]) and len(r["created"]) == 3)
    byk = {c["key"]: c for c in r["created"]}
    ok("input order preserved", [c["key"] for c in r["created"]] == ["d-cli", "d-collect", "d-classify"])
    ok("all born autopilot + cohort",
       all(dev.read_work_item(root, c["id"])["autopilot"] and
           dev.read_work_item(root, c["id"])["cohort"] == r["cohort"] for c in r["created"]))
    ok("edges resolved key→minted id",
       byk["d-collect"]["after"] == [byk["d-cli"]["id"]]
       and set(byk["d-classify"]["after"]) == {byk["d-cli"]["id"], byk["d-collect"]["id"]})
    ok("ready item active, dependents parked",
       byk["d-cli"]["status"] == "active"
       and byk["d-collect"]["status"] == "awaiting_upstream"
       and byk["d-classify"]["status"] == "awaiting_upstream"
       and r["running"] == [byk["d-cli"]["id"]] and len(r["waiting"]) == 2)
    # cycle + unknown-edge + empty are refused loudly (edges validated before any write)
    for bad, label in (
        ([{"key": "a", "title": "A", "after": ["b"]}, {"key": "b", "title": "B", "after": ["a"]}], "cycle"),
        ([{"key": "a", "title": "A", "after": ["ghost"]}], "unknown edge"),
        ([], "empty batch"),
        ([{"key": "a", "title": "A"}, {"key": "a", "title": "dup"}], "duplicate key"),
    ):
        try:
            dev.itemize_launch(root / f"sub-{label.replace(' ', '')}", bad)
            ok(f"itemize refuses {label}", False)
        except ValueError:
            ok(f"itemize refuses {label}", True)


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
    wid = dev.create_work_item(root, "paging item", kind="implementation")["id"]
    dev.set_work_item_status(root, wid, "awaiting_human")
    data = dev.read_all(root)
    g = data["glance"]
    ok("awaiting_human bucket", any(x["id"] == wid for x in g["awaiting_human"]))
    ok("by_status counts", g["by_status"].get("awaiting_human", 0) >= 1)
    ok("glance shape", set(g) >= {"by_status", "by_phase", "active", "awaiting_human"})
    # Dependency has ONE expression: a `blocking` branch-off pausing the parent at
    # awaiting_child. No dependency-id list, so no `blocked` bucket.
    ok("no blocked vocabulary", "blocked" not in g and "blocked_by" not in data["work_items"][0])


def test_item_scale(dev: DevKnowledgeService, tmp: Path) -> None:
    """Scale is the CONTENT dial: triage's judgment, carried into every later phase's turn.

    A small item is told a boundary and what to do when the boundary is wrong."""
    from superme_agent.core import kernel_speech as ks
    print("item scale — the content dial")
    root = tmp / "scale-dev"
    wid = dev.create_work_item(root, "A tiny fix", kind="implementation")["id"]
    born = dev.read_work_item(root, wid)
    ok("born standard, unjudged", kp.item_scale(born) == "standard" and not born.get("scale_reason"))
    ok("an item with no scale field at all still reads standard", kp.item_scale({}) == "standard")
    ok("a garbage scale reads standard, never raises", kp.item_scale({"scale": "enormous"})
       == "standard")

    for bad, label in ((("huge", "x"), "unknown value"), (("small", "   "), "blank reason")):
        try:
            dev.set_work_item_scale(root, wid, *bad)
            ok(f"{label} refused", False)
        except ValueError:
            ok(f"{label} refused", True)

    dev.set_work_item_scale(root, wid, "small", "one  line\tin status_router")
    small = dev.read_work_item(root, wid)
    ok("scale + reason persist, reason collapsed to one line",
       kp.item_scale(small) == "small" and small["scale_reason"] == "one line in status_router")
    # `re.sub` parses backslash escapes in its replacement, so a curly quote killed the write.
    fancy = "the owner\u2019s ask names it \u2014 one \u201cquiet\u201d guard"
    dev.set_work_item_scale(root, wid, "small", fancy)
    ok("a reason with typographic punctuation survives",
       dev.read_work_item(root, wid)["scale_reason"] == fancy)
    dev.set_work_item_title(root, wid, "Don\u2019t print \u201cOVER BUDGET\u201d under --quiet")
    ok("a title with typographic punctuation survives",
       "OVER BUDGET" in dev.read_work_item(root, wid)["title"])

    # An item minted before the field existed has no `scale:` line to rewrite.
    legacy = dev.create_work_item(root, "Older item", kind="implementation")["id"]
    p = root / "work-items" / legacy / "item.md"
    p.write_text(re.sub(r"(?m)^scale.*\n", "", p.read_text(encoding="utf-8")), encoding="utf-8")
    ok("legacy item reads standard", kp.item_scale(dev.read_work_item(root, legacy)) == "standard")
    dev.set_work_item_scale(root, legacy, "small", "inserted, not rewritten")
    ok("...and can still be judged (insert path)",
       kp.item_scale(dev.read_work_item(root, legacy)) == "small")

    # The preamble is where scale actually changes behaviour — it rides every phase's turn.
    at_small = ks.work_item_preamble(wid, small, root / "work-items" / wid, interactive=False)
    at_std = ks.work_item_preamble(wid, dict(small, scale="standard"),
                                   root / "work-items" / wid, interactive=False)
    # Substring-matching "scal" catches "escalation" in the run protocol — pin the LINE, not a stem.
    ok("standard costs no preamble floor", "scaled `small`" not in at_std)
    ok("small names a read boundary and a write bound",
       "small" in at_small.lower() and "read" in at_small.lower())
    # Structure stays whole at small, so an agent with nothing to say has somewhere to put that.
    ok("small gives overflow an escape hatch instead of filler",
       "misjudged" in at_small and "pad" in at_small)


def main() -> None:
    dev = DevKnowledgeService()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        root = tmp / "dev"
        test_kind_profiles()
        test_item_contract(dev, root)
        test_terminal(dev, root)
        test_status_router()
        test_peer_sequencing()
        test_after_field(dev, root)
        test_autopilot_decision()
        test_autopilot_concurrency()
        test_autopilot_field(dev, root)
        test_advance_review_status(dev, root, tmp)
        test_deputy(tmp)
        test_itemize_launch(dev, root)
        test_inbox_spawned_from(tmp)
        test_glance(dev, root)
        test_item_scale(dev, tmp)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
