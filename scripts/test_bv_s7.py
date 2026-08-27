"""The one way back from review: `revise` to plan, and the revision grammar.

Scope is per CHANGE, because one review carries several concerns and redesigning one part must
not reset another part's progress.

Run: PYTHONPATH=. python -m scripts.test_bv_s7
"""

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from superme_agent.core import plan_revision as PR
from superme_agent.daemon.services import runs as R
from superme_agent.daemon.services.runs import lifecycle as RL, phases as RP
from superme_agent.harness.tools.dev_tools import _revise_plan
from scripts.sources import src

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


PLAN = """---
artifact: plan
---
# Plan — s7 probe

## Intent
Sum the ledger.

## Design
Read the ledger, sum the amounts, print the total.

## Tasks
- [x] t1 — read the ledger file
- [ ] t2 — print the total
- [ ] t3 — only if t1 finds a failure: root-cause it against the traced code path and apply
  the minimal fix in `ledger/commands.py`, extending the failing test to cover it. Record what
  was found in the cycle report.

## Decisions & clarifications
<!-- append-only -->

## Verification plan
depth: checks
reason: one contained check suffices
env: none

### probe-value
- proves: the probe reports the value the item was built to report
- traces: d-s7
- mode: command
- scenario: run the probe
- expect: the probe prints exactly s7 and exits zero
"""

ITEM_MD = """---
id: it7
title: S7 revise probe
kind: implementation
status: awaiting_human
phase: review
created_at: 2026-07-17T00:00:00Z
updated_at: 2026-07-17T00:00:00Z
---
Revise probe item.
"""

FEEDBACK = "Handle the empty-ledger case — `sum` on a fresh repo should print 0, not crash."


def make_dev_root(tmp: Path, name: str, *, phase: str = "review") -> Path:
    dev_root = tmp / name
    d = dev_root / "work-items" / "it7"
    (d / "artifacts").mkdir(parents=True)
    (d / "item.md").write_text(ITEM_MD.replace("phase: review", f"phase: {phase}"), encoding="utf-8")
    (d / "artifacts" / "plan.md").write_text(PLAN, encoding="utf-8")
    return dev_root


class _Store:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def log_event(self, context_id, kind, text, **kw) -> None:
        self.events.append((kind, text, kw))


# ---------------------------------------------------------------------------- plan_revision core

def _tasks_of(body: str) -> str:
    return re.search(r"(?ms)^## Tasks\n(.*?)(?=^## |\Z)", body).group(1)


def test_plan_revision(tmp: Path) -> None:
    print("core.plan_revision — the revision grammar")
    d = make_dev_root(tmp, "root-rev") / "work-items" / "it7"
    text = PR.plan_path(d).read_text(encoding="utf-8")

    def one(scope, ops=None, **extra):
        return [{"area": "the empty-ledger case", "scope": scope, "note": "n",
                 **({"ops": ops} if ops is not None else {}), **extra}]

    # Refusals — each writes nothing.
    ok("an empty change list is refused (a revision must say something)",
       any("at least one change" in i for i in PR.validate(text, [])))
    ok("a `resume` change carrying ops is refused — that IS the proportionality guard",
       any("makes NO plan edit" in i for i in
           PR.validate(text, one("resume", [{"op": "append", "section": "Design",
                                             "content": "x"}]))))
    ok("...and a bare `resume` change is legal: 'keep going' needs no edit",
       PR.validate(text, one("resume")) == [])
    ok("a targeted change with NO ops is refused (that case is `resume`)",
       any("owes at least one op" in i for i in PR.validate(text, one("targeted", []))))
    ok("a change with no `area` is refused (a dropped concern must be visible)",
       any("`area` is required" in i for i in
           PR.validate(text, [{"scope": "resume", "note": "n"}])))
    ok("a change with no `note` is refused",
       any("`note` is required" in i for i in
           PR.validate(text, [{"area": "a", "scope": "resume"}])))
    ok("scope is per CHANGE, and an unknown one is refused",
       any("scope must be one of" in i for i in PR.validate(text, one("everything"))))
    ok("a Tasks section rewrite is refused at targeted scope (checkboxes are earned progress)",
       any("task-level ops" in i for i in
           PR.validate(text, one("targeted", [{"op": "update", "section": "Tasks",
                                               "content": "- [ ] t1 — x"}]))))
    ok("...and allowed at redesign scope (the old tasks are void)",
       PR.validate(text, one("redesign", [{"op": "update", "section": "Tasks",
                                           "content": "- [ ] t1 — x"}])) == [])
    ok("Decisions & clarifications stays append-only",
       any("append-only" in i for i in
           PR.validate(text, one("targeted", [{"op": "update",
                                               "section": "Decisions & clarifications",
                                               "content": "x"}]))))
    ok("a section that doesn't exist is refused (a revise never invents structure)",
       any("no section" in i for i in
           PR.validate(text, one("targeted", [{"op": "update", "section": "Nope",
                                               "content": "x"}]))))
    ok("the code-owned revision history is not editable",
       any("written by code" in i for i in
           PR.validate(text, one("targeted", [{"op": "append", "section": "Revision log",
                                               "content": "x"}])))
       and any("written by code" in i for i in
               PR.validate(text, one("targeted", [{"op": "append", "section": "Revision r1",
                                                   "content": "x"}]))))
    ok("an unknown task id is refused",
       any("no task" in i for i in
           PR.validate(text, one("targeted", [{"op": "edit_task", "task": "t9",
                                               "content": "x"}]))))
    ok("nothing was written by any refusal", PR.plan_path(d).read_text(encoding="utf-8") == text)

    # ONE revision, THREE scopes: redesigning one part must not reset the progress another part
    # earned.
    res = PR.revise(d, feedback=FEEDBACK, directive="handle the empty ledger, keep streaming",
                    still_in_force="nothing", concerns=["budget", "vet_failure"], spend=412330,
                    changes=[
        {"area": "budget", "scope": "resume", "note": "3 cycles retried the same route; try more"},
        {"area": "empty-ledger design", "scope": "targeted", "note": "0 instead of a crash",
         "ops": [{"op": "append", "section": "Design", "content": "An empty ledger sums to 0."},
                 {"op": "edit_task", "task": "t2",
                  "content": "print the total, 0 when the ledger is empty"},
                 {"op": "add_task", "content": "cover the empty-ledger case in the checks"}]},
    ])
    body = PR.plan_path(d).read_text(encoding="utf-8")
    ok("revision id assigned", res["revision"] == "r1")
    ok("t1 keeps its `- [x]` — build's progress survived the revision", "- [x] t1 —" in body)
    ok("t2's text changed, its state didn't", "- [ ] t2 — print the total, 0 when" in body)
    ok("the added task got the next id", "- [ ] t4 — cover the empty-ledger case" in body)
    ok("the section op appended, it did not replace",
       "Read the ledger, sum the amounts" in body and "An empty ledger sums to 0." in body)
    ok("untouched sections are byte-identical (nothing wrote them)",
       "## Intent\nSum the ledger." in body)
    ok("the block carries feedback verbatim, the directive, still-in-force, and per-change scopes",
       "## Revision r1 —" in body and FEEDBACK in body
       and "- directive: handle the empty ledger, keep streaming" in body
       and "- still in force: nothing" in body
       and "  - budget — resume — 3 cycles retried" in body
       and "  - empty-ledger design — targeted — 0 instead of a crash" in body
       and "    - Tasks — t2 edited" in body)
    ok("`concerns` come from code and land on the block, not from the agent's prose",
       "- concerns: budget, vet_failure" in body)
    ok("the block records the spend boundary that opens the generation",
       "- spend_at: 412330" in body and PR.spend_at(d) == 412330)
    ok("the code-owned index carries the shape of the history and NO prose",
       "- r1 — " in body and "concerns: budget, vet_failure" in body
       and "scopes: resume, targeted" in body
       and "3 cycles retried" not in _split_log(body))
    ok("revisions() reads the block back", PR.revisions(d) == ["r1"]
       and PR.current_revision(d) == "r1")
    heads = [ln for ln in body.splitlines() if re.match(r"^##\s", ln)]
    ok("the live zone is pinned LAST — nothing below Tasks can contradict it",
       heads[-2:] == ["## Tasks", "## Verification plan"]
       and heads[-3].startswith("## Revision r1 —"), heads)
    ok("...and the index sits above the blocks, so the history reads in order",
       heads.index("## Revision log") < len(heads) - 3)

    # A task is a BLOCK: a line-wise remove would glue continuation lines onto the task above.
    PR.revise(d, feedback="drop the dead task", directive="ignore t3", still_in_force="r1 holds",
              changes=one("targeted", [{"op": "remove_task", "task": "t3"}]))
    body = PR.plan_path(d).read_text(encoding="utf-8")
    tasks = _tasks_of(body)
    ok("remove_task takes the whole wrapped block, leaving no orphan lines",
       "t3" not in tasks and "minimal fix in" not in tasks and "cycle report" not in tasks,
       tasks)
    ok("...and the tasks above it are untouched, checkboxes and all",
       "- [x] t1 — read the ledger file" in tasks and "- [ ] t2 — print the total" in tasks)
    ok("the removal leaves no blank hole behind", "\n\n" not in tasks.strip())

    # A redesign names what it voids: `_reset_checkboxes` is DELETED, so the ops ARE the state.
    PR.revise(d, feedback="the approach is wrong entirely", directive="stream it",
              still_in_force="r1's empty-ledger rule still holds",
              changes=[{"area": "approach", "scope": "redesign", "note": "stream instead",
                        "superseded": "all of r1",
                        "ops": [{"op": "update", "section": "Design",
                                 "content": "Stream the ledger instead."},
                                {"op": "remove_task", "task": "t1"},
                                {"op": "add_task", "content": "emit the running total"}]}])
    body = PR.plan_path(d).read_text(encoding="utf-8")
    ok("redesign replaced the design body", "Stream the ledger instead." in body
       and "Read the ledger, sum the amounts" not in body)
    ok("a redesign voids only what it NAMES — the ticked t1 is gone, the rest kept its state",
       "t1" not in _tasks_of(body) and "- [ ] t2 — print the total" in _tasks_of(body))
    ok("a freed task number is never reused (SuperMe-Task trailers are permanent)",
       "- [ ] t5 — emit the running total" in _tasks_of(body))

    ok("redesign records what is superseded", "    - supersedes: all of r1" in body)
    ok("blocks accumulate — r1 is still there in full, and r3 says what still binds",
       PR.revisions(d) == ["r1", "r2", "r3"] and FEEDBACK in body
       and "- still in force: r1's empty-ledger rule still holds" in body)
    # ...and not one revision later either: the high-water mark reads the ids HISTORY names, not
    # the live list.
    PR.revise(d, feedback="one more task", directive="add the flag test", still_in_force="r3 holds",
              changes=one("targeted", [{"op": "add_task", "content": "the follow-up"}]))
    body = PR.plan_path(d).read_text(encoding="utf-8")
    ok("...and the mark counts up across revisions, never back into a retired id",
       "- [ ] t6 — the follow-up" in _tasks_of(body)
       and PR.task_high_water(body) == 6)


def _split_log(body: str) -> str:
    return re.search(r"(?ms)^## Revision log\n(.*?)(?=^## )", body).group(1)


def test_pre_grammar_plan_migrates(tmp: Path) -> None:
    print("a plan authored before the revision grammar migrates in place, losing nothing")
    from superme_agent.core import artifacts as A
    d = make_dev_root(tmp, "root-legacy", phase="plan") / "work-items" / "it7"
    # The earlier shape: `## Tasks` mid-document, and ONE `## Revisions` section holding `### r1`.
    p = PR.plan_path(d)
    p.write_text(p.read_text(encoding="utf-8").rstrip() + "\n\n## Revisions\n### r1 — 2026-07-29T19:21:02 — targeted\n"
                 "- feedback: the first round's words\n- Design (append): a point\n", encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    ok("the legacy log still counts as a recorded revision (no retroactive red gate)",
       PR.revisions(d) == ["r1"] and PR.current_revision(d) == "r1")
    ok("a legacy block carries no spend reading, so the item meters from birth as it always did",
       PR.spend_at(d) == 0)

    PR.revise(d, feedback="second round", directive="fix the filtered path", still_in_force="r1 holds",
              concerns=["vet_failure"], spend=133742,
              changes=[{"area": "filter", "scope": "targeted", "note": "sum filtered rows",
                        "ops": [{"op": "append", "section": "Design", "content": "Filtered rows only."}]}])
    after = p.read_text(encoding="utf-8")
    ok("the new block does NOT reuse the legacy number — r1's history stays r1's",
       PR.revisions(d) == ["r1", "r2"] and "## Revision r2 —" in after
       and "## Revision r1 —" not in after)
    heads = [ln for ln in after.splitlines() if ln.startswith("## ")]
    ok("the live zone was MOVED to the end, and the legacy log joined the history zone",
       heads[-2:] == ["## Tasks", "## Verification plan"]
       and heads.index("## Revisions") < heads.index("## Revision log"))
    for sec in ("Intent", "Tasks", "Decisions & clarifications", "Verification plan", "Revisions"):
        ok(f"`## {sec}` came through the move byte-identical",
           A.split_sections(before).get(sec, "").strip()
           == A.split_sections(after).get(sec, "").strip())
    ok("every reader still parses the migrated file", A.self_check(d, "plan",
                                                                  item_kind="implementation") == []
       and [c["id"] for c in A.parse_vet_plan(after)["checks"]] == ["probe-value"]
       and [(t["id"], t["done"]) for t in A.parse_tasks(after)][0] == ("t1", True))
    ok("a second pass is idempotent on the layout — it does not move anything again",
       [ln for ln in PR._pin_live_zone(after).splitlines() if ln.startswith("## ")] == heads)


def test_generation_scoping(tmp: Path) -> None:
    print("a revision opens a GENERATION — both breakers re-zero")
    from superme_agent.core import artifacts as A
    d = make_dev_root(tmp, "root-gen", phase="plan") / "work-items" / "it7"

    # Generation 1: two cycles, the second ending on the budget breaker.
    A.scaffold_cycle(d, title="S7")
    A.append_cycle_outcome(d, evidence="failed", decision="build", reason="one check failed",
                           fingerprint="fp-a", tokens=100, budget=500)
    A.scaffold_cycle(d, title="S7")
    A.append_cycle_outcome(d, evidence="failed", decision="review", reason="budget",
                           loop_exit="budget", fingerprint="fp-a", tokens=500, budget=500)
    ok("the typed exit is on the RECORD, not inferred from prose",
       A.read_cycle_outcomes(d)[-1]["exit"] == "budget")
    ok("`concerns` are read off that record — the agent never tags them",
       PR.derive_concerns(d) == ["budget", "vet_failure"])
    ok("the whole-life read still sees both cycles (the report feeds want it)",
       len(A.read_cycle_outcomes(d)) == 2)

    # The revision. Generation 2's cycle reports stamp `plan_revision: r1`.
    PR.revise(d, feedback="try more", directive="same plan, another generation",
              still_in_force="nothing", concerns=PR.derive_concerns(d), spend=500,
              changes=[{"area": "budget", "scope": "resume", "note": "it looked close"}])
    ok("generation 1's cycles no longer count against generation 2",
       A.read_cycle_outcomes(d, revision="r1") == []
       and len(A.read_cycle_outcomes(d, revision="")) == 2)
    ok("the recurrence guard therefore starts clean — fp-a is generation 1's history",
       not [a for a in A.read_cycle_outcomes(d, revision="r1")
            if a.get("fingerprint") == "fp-a"])
    ok("the budget meter starts clean too: spent = meter − spend_at",
       max(0, 500 - PR.spend_at(d)) == 0)
    A.scaffold_cycle(d, title="S7")
    ok("the new cycle report stamps the revision it implements",
       A.cycle_reports(d)[-1]["revision"] == "r1")
    A.append_cycle_outcome(d, evidence="failed", decision="build", reason="still failing",
                           fingerprint="fp-a", tokens=40, budget=500)
    ok("...and generation 2's own history counts normally from there",
       len(A.read_cycle_outcomes(d, revision="r1")) == 1)
    ok("a second revision with nothing mechanical on record reads as owner judgment",
       PR.derive_concerns(d) == ["vet_failure"])


# ---------------------------------------------------------------------------- the tool

class _Spine:
    """The meter the tool reads to stamp the generation boundary."""

    def item_phase_tokens(self, repo_id, item_id, phases=("build", "vet")) -> int:
        return 90_210


def _args(**over) -> dict:
    base = {"item_id": "it7", "feedback": FEEDBACK, "directive": "handle the empty ledger",
            "still_in_force": "nothing",
            "changes": [{"area": "the empty-ledger case", "scope": "targeted", "note": "0 not crash",
                         "ops": [{"op": "append", "section": "Design", "content": "x"}]}]}
    return {**base, **over}


def test_tool(tmp: Path) -> None:
    print("revise_plan — the tool handler")
    dev_root = make_dev_root(tmp, "root-tool", phase="plan")
    store = _Store()
    tool = _revise_plan(store=store, context_id="c", dev_root=dev_root, bound_item_id="it7",
                        spine=_Spine())

    out = asyncio.run(tool(_args()))
    text = out["content"][0]["text"]
    ok("happy path applies and reports what changed",
       "revised as r1" in text and "Design (append)" in text, text[:200])
    ok("the reply names the concerns CODE derived, not the agent's",
       "Concerns on record: owner_judgment" in text, text[:400])
    ok("the event carries the revision, its scopes and its concerns",
       any(k == "plan.revised" and kw["meta"]["revision"] == "r1"
           and kw["meta"]["scopes"] == ["targeted"]
           and kw["meta"]["concerns"] == ["owner_judgment"] for k, _, kw in store.events))
    ok("the spend boundary is read from the spine, never from the agent",
       "- spend_at: 90210" in PR.plan_path(dev_root / "work-items" / "it7").read_text(encoding="utf-8"))

    out = asyncio.run(tool(_args(changes=[{"area": "a", "scope": "targeted", "note": "n",
                                           "ops": [{"op": "update", "section": "Tasks",
                                                    "content": "- [ ] t1 — x"}]}])))
    ok("a refusal names the rule and writes nothing",
       "Revision rejected" in out["content"][0]["text"]
       and PR.revisions(dev_root / "work-items" / "it7") == ["r1"])

    for field in ("feedback", "directive", "still_in_force"):
        out = asyncio.run(tool(_args(**{field: "  "})))
        ok(f"empty `{field}` refused (the block would record a complaint, not an instruction)",
           "required" in out["content"][0]["text"])

    # PHASE GUARD. Folding feedback into plan.md without leaving review changes the contract with
    # nothing re-running against it.
    review_root = make_dev_root(tmp, "root-phase", phase="review")
    at_review = _revise_plan(store=_Store(), context_id="c", dev_root=review_root,
                             bound_item_id="it7", spine=_Spine())
    out = asyncio.run(at_review(_args()))
    msg = out["content"][0]["text"]
    ok("a revise at REVIEW is refused and points at the one way back",
       "PLAN phase" in msg and "revise" in msg and PR.revisions(
           review_root / "work-items" / "it7") == [], msg[:160])
    plan_root = make_dev_root(tmp, "root-plan", phase="plan")
    at_plan = _revise_plan(store=_Store(), context_id="c", dev_root=plan_root, bound_item_id="it7",
                           spine=_Spine())
    out = asyncio.run(at_plan(_args()))
    ok("...and the same call at PLAN goes through",
       "revised as r1" in out["content"][0]["text"])

    unbound = _revise_plan(store=_Store(), context_id="c", dev_root=dev_root, bound_item_id=None,
                           spine=_Spine())
    out = asyncio.run(unbound(_args()))
    ok("unbound session refused (worker tool-scoping)",
       "no item write-tools" in out["content"][0]["text"]
       or "bound" in out["content"][0]["text"])


# ---------------------------------------------------------------------------- routing

@dataclass
class _Ctx:
    internal_root: Path
    cwd: Path
    id: str = "c"
    mode: str = "dev"


def test_revise_outcome_routes(tmp: Path) -> None:
    print("end_run — the `revise` outcome is the one way back")
    _RUNS_SRC = src("superme_agent/daemon/services/runs.py")
    fired: list = []
    advanced: list = []
    orig_fire, orig_spine, orig_store, orig_status = (
        RP.fire_phase_feedback, RL._spine, RL._dev_store, RL._set_status)

    class _Spine:
        def live_run(self, *a): return {"feature": "review"}
        def finish_item_run(self, *a, **kw): return 1
        def run_tokens(self, rid): return 0

    try:
        RP.fire_phase_feedback = lambda cid, iid, **kw: fired.append((iid, kw)) or True
        RL._spine, RL._dev_store = _Spine(), _Store()
        RL._set_status = lambda *a, **kw: None
        RL.end_run(None, "c", "it7", 0, status="awaiting_human", outcome="revise",
                    summary="the approach misses the empty case")
        ok("revise routes the conversation back to plan, owner-attributed",
           len(fired) == 1 and fired[0][1]["phase"] == "review" and fired[0][1]["by"] == "owner"
           and fired[0][1]["feedback"] == "the approach misses the empty case")
        ok("...and does NOT also fall through to the autopilot advance", not advanced)
        # A `revise` from elsewhere is not this function's to route: it would log a review that
        # never ran.
        fired.clear()
        _Spine.live_run = lambda self, *a: {"feature": "build", "phase": "build"}
        RL.end_run(None, "c", "it7", 0, status="active", outcome="revise",
                    summary="the plan's commands point at the wrong tree")
        ok("a build's revise is left to the loop driver — one writer per transition", not fired)

        # THE OWNER'S OWN ROUTE BACK. An interactive turn at review is `feature='chat'`, so a
        # branch reading the FEATURE never fires.
        fired.clear()
        _Spine.live_run = lambda self, *a: {"feature": "chat", "phase": "review"}
        RL.end_run(None, "c", "it7", 0, status="active", outcome="revise",
                    summary="you covered storage and stopped; the callers are part of the surface")
        ok("an OWNER's revise, concluded in chat at review, routes exactly like the run's",
           len(fired) == 1 and fired[0][1]["phase"] == "review" and fired[0][1]["by"] == "owner")
        ok("…because routing reads the run's PHASE, never the surface that ran it",
           'run_phase = str((info or {}).get("phase") or kind)' in _RUNS_SRC
           and 'outcome == "revise" and run_phase == "review"' in _RUNS_SRC)
        # A chat turn at a phase that is NOT review still belongs to that phase's driver.
        fired.clear()
        _Spine.live_run = lambda self, *a: {"feature": "chat", "phase": "build"}
        RL.end_run(None, "c", "it7", 0, status="active", outcome="revise", summary="nope")
        ok("…and a chat turn at build is still the loop's to route", not fired)
    finally:
        RP.fire_phase_feedback, RL._spine, RL._dev_store, RL._set_status = (
            orig_fire, orig_spine, orig_store, orig_status)


def test_fire_phase_feedback_owner(tmp: Path) -> None:
    print("fire_phase_feedback(by='owner') — flip review→plan, owner attribution, begin plan run")
    from superme_agent.gateway import contexts as GW
    from superme_agent.core.dev_knowledge import DevKnowledgeService

    dev_root = make_dev_root(tmp, "root-fire")
    real = tmp / "internal"
    (real / "dev" / "work-items").mkdir(parents=True)
    import shutil
    shutil.copytree(dev_root / "work-items" / "it7", real / "dev" / "work-items" / "it7")

    store = _Store()
    began: list = []
    tasks: list = []

    class _Spine:
        def effective_model(self, cid, **kw): return "opus"
        def effective_effort(self, cid, **kw): return "medium"
        def stamp_session_item(self, *a): pass

    orig = (GW.resolve, RP._dev_store, RP._spine, RP.begin_run, RP._run_deputy_feedback_turn)
    try:
        GW.resolve = lambda cid, mode: _Ctx(internal_root=real, cwd=tmp, id=cid)
        RP._dev_store = store
        RP._spine = _Spine()
        RP.begin_run = lambda *a, **kw: began.append((a, kw)) or 7

        async def fake_turn(*a, **kw):
            tasks.append((a, kw))

        RP._run_deputy_feedback_turn = fake_turn

        async def go():
            out = RP.fire_phase_feedback("c", "it7", phase="review", feedback=FEEDBACK,
                                        digest="DIGEST", by="owner")
            await asyncio.sleep(0)   # let the created task run
            return out
        started = asyncio.run(go())

        ok("fire returned True", started is True)
        dev = DevKnowledgeService()
        ok("phase flipped review→plan", dev.read_work_item(real / "dev", "it7")["phase"] == "plan")
        ok("review.route logged as actor=owner",
           any(k == "review.route" and kw.get("actor") == "owner"
               and kw["meta"].get("by") == "owner" for k, _, kw in store.events))
        ok("owner.query marker logged (actor=owner), NOT deputy.query",
           any(k == "owner.query" and kw.get("actor") == "owner" for k, _, kw in store.events)
           and not any(k == "deputy.query" for k, _, kw in store.events))
        ok("a plan-phase run was opened + the feedback turn dispatched",
           began and began[0][1].get("phase") == "plan" and len(tasks) == 1)
        from superme_agent.core import kernel_speech as KS
        trigger = KS.phase_feedback_trigger("it7", "t", "plan", "plan", FEEDBACK)
        ok("the plan trigger names the ONE sanctioned way to change plan.md, and the scope ladder",
           "revise_plan" in trigger and "never rewrite the file" in trigger
           and "`resume`" in trigger and "`redesign`" in trigger)
        ok("...and a non-plan phase's trigger doesn't carry that instruction",
           "revise_plan" not in KS.phase_feedback_trigger("it7", "t", "triage", "triage", FEEDBACK))
    finally:
        GW.resolve, RP._dev_store, RP._spine, RP.begin_run, RP._run_deputy_feedback_turn = orig


# ---------------------------------------------------------------------------- the gate check

def test_gate_check(tmp: Path) -> None:
    print("pre-main gate — every feedback round owes a revision block")
    from superme_agent.core import gate_briefs as GB
    dev_root = make_dev_root(tmp, "root-gate", phase="plan")
    item = {"id": "it7", "title": "S7", "kind": "implementation", "phase": "plan",
            "status": "awaiting_human"}
    item_dir = dev_root / "work-items" / "it7"
    rounds = [{"kind": "review.route", "text": "routed", "meta": {}}]

    def check(events):
        s = GB.gate_state(item, item_dir, dev_root, None, events=events)
        return next((c for c in s["checks"] if c["criterion"] == "revisions_recorded"), None)

    ok("no feedback round → the check isn't raised at all", check([]) is None)
    c = check(rounds)
    ok("a round with no revision block fails, and says how to fix it",
       c is not None and not c["ok"] and "revise_plan" in c["detail"])
    PR.revise(item_dir, feedback=FEEDBACK, directive="handle it", still_in_force="nothing",
              changes=[{"area": "a", "scope": "targeted", "note": "n",
                        "ops": [{"op": "append", "section": "Design", "content": "x"}]}])
    c = check(rounds)
    ok("once the pass is recorded, the check passes", c is not None and c["ok"] and "r1" in c["detail"])
    # Visible and named, but it does not grey Approve: what it asks for is a conversation, not a
    # click.
    ok("...and an unrecorded revision never blocks Approve", not check(rounds)["blocking"])

    # ONLY A KIND THAT VETS IS ASKED ABOUT ITS EVIDENCE: research has none, so the row blocked
    # forever.
    def review_criteria(kind):
        it = {"id": "it7", "title": "S7", "kind": kind, "phase": "review",
              "status": "awaiting_human"}
        return {c["criterion"] for c in GB.gate_state(it, item_dir, dev_root, None,
                                                      events=[])["checks"]}

    ok("an implementation review still asks whether the evidence is fresh",
       "evidence_fresh" in review_criteria("implementation"))
    ok("...and a research review does not — it has no vet phase to answer it",
       "evidence_fresh" not in review_criteria("research"))


# ---------------------------------------------------------------------------- contracts

def test_decision_bubbles() -> None:
    print("deputy decisions — one headline each, in the chat; the runbook stays in the tab")
    from superme_agent.daemon.services import deputy as D
    ok("a headline is the first paragraph only",
       D._headline("Needs your call on the contract.\n\nlong runbook here")
       == "Needs your call on the contract.")
    ok("a headline is capped, never a wall", len(D._headline("x" * 500)) <= 241)

    # The cap is enforced at the SOURCE too: a stated limit that nothing checks is a suggestion.
    from superme_agent.harness.tools.run_tools import _submit_gate_verdict
    sink: dict = {}
    verdict = _submit_gate_verdict(verdict_sink=sink)
    args = {"machine": {"decision": "approve", "gate": "plan"},
            "user": {"checked": "plan.md, the evidence ledger", "because": "x" * 260}}
    out = asyncio.run(verdict(args))
    ok("an over-long `because` is refused, and nothing lands in the sink",
       "under 200" in out["content"][0]["text"] and not sink)
    args["user"]["because"] = "r3 fixed a text-hygiene defect only; the design I approved is unchanged."
    asyncio.run(verdict(args))
    ok("...and a one-line ground passes", sink.get("verdict", {}).get("decision") == "approve")

    store = _Store()
    orig = (D._dev_store, D._dev)
    try:
        D._dev_store = store
        D._dev = type("D", (), {"set_work_item_status": staticmethod(lambda *a, **k: None)})()
        ctx = _Ctx(internal_root=Path("/tmp"), cwd=Path("/tmp"))
        D._do_escalate(ctx, "c", "it7", "review",
                       {"because": "the plan redefines the deliverable's contract",
                        "escalation": "Situation: …\n\nConcern: …\n\nDecide: …",
                        "checked": "plan.md, roadmap.md"},
                       reason="deputy_escalated")
        meta = next(kw["meta"] for k, _, kw in store.events if k == "deputy.escalate")
        ok("the bubble is one line — the WHY, not the runbook",
           "\n" not in meta["speech"] and "the plan redefines" in meta["speech"]
           and "Situation:" not in meta["speech"])
        ok("...and the full runbook is still on the event for the Deputy tab",
           "Situation:" in meta["escalation"] and "Decide:" in meta["escalation"])
    finally:
        D._dev_store, D._dev = orig

    # The work-item chat renders TimelineView, never MessageList, so the channel rules live there.
    tl = src("web/frontend/src/features/chat/TimelineView.tsx")
    ok("decisions render as bubbles", "deputy.approve" in tl and "deputy.escalate" in tl
       and "deputyDecisions" in tl)
    # A decision is an EVENT: it has a time and a subject, and the thread must honour both.
    ok("...labelled with the gate they judged, not with wherever the item is now",
       "e.meta?.gate" in tl and "decisionBubbles" not in tl)
    ok("...and placed by time, not appended after the thread",
       "...deputyDecisions.map" in tl and "const all = [...settled, ...liveBubbles]" in tl)
    ok("a send-back turn renders as the deputy (it really was sent to the agent)",
       "deputy.query" in tl and "fromDeputy" in tl)
    ok("the deputy's own judging run never reaches the channel — history AND live",
       "if (run.feature === 'deputy') continue" in tl
       and "runFeature === 'deputy' && f.kind === 'reply') continue" in tl)


def test_routing_rule_is_per_turn() -> None:
    print("the offer rule rides the TURN, not the skill the turn never loads")
    from superme_agent.core.kernel_speech import work_item_preamble
    item = {"title": "t", "kind": "implementation"}
    MARK = "your next turn is that offer"
    # The routing turn invokes no skill, so a rule living only in the skill is not in it.
    at_review = work_item_preamble("it7", {**item, "phase": "review"}, "/tmp/it7")
    ok("an interactive review turn carries the routing precondition on every turn",
       MARK in at_review and "or your offer and their yes" in at_review)
    ok("...and the ask-back on what the owner passed over",
       "name what they have NOT addressed" in at_review)
    ok("a kernel-fired review run doesn't get it (nobody is there to offer to)",
       MARK not in work_item_preamble("it7", {**item, "phase": "review"}, "/tmp/it7",
                                      interactive=False))
    ok("no other phase carries it — `revise` is review's outcome alone",
       not any(MARK in work_item_preamble("it7", {**item, "phase": p}, "/tmp/it7")
               for p in ("triage", "plan", "build", "vet", "close")))


def test_hold_and_compaction_hooks() -> None:
    print("a chat turn never clears a hold · the compaction trigger covers background runs")
    ws = src("superme_agent/daemon/routers/ws.py")
    # A parked item stays parked: flipping it to active would read IN PROGRESS and drop it from
    # the attention feed.
    ok("a chat turn rests a PARKED item back at its hold, not at active",
       'if str((item or {}).get("status")) == "awaiting_human"' in ws
       and 'and outcome != "revise" else "active"' in ws)
    ok("...and `revise` — the one outcome that MOVES the item — still drops it",
       'rest_status = ("awaiting_human"' in ws)

    # ...and the hold is read from the FACTS, so a row that lost one still renders honestly.

    # The rule lives beside the run table, not in the FE: one screen once answered it three ways.
    attn = src("superme_agent/core/attention.py")
    ok("`active` at a gate with nothing running pages as needs_you — in the DAEMON",
       'str(it.get("status")) == "active"' in attn
       and 'str(it.get("phase") or "") in GATE_FOR_PHASE' in attn)
    ok("...ranked below the live tiers, so a gate WITH a run is never a stall",
       attn.index('elif iid in running_ids') < attn.index('str(it.get("status")) == "active"'))
    ok("...and build/vet are excluded — the loop chains them, mid-flight is not parked",
       "GATE_FOR_PHASE = {\"triage\"" in src("superme_agent/core/gate_briefs.py"))
    common = src("web/frontend/src/features/dev/common.tsx")
    # Matched on the CONSTRUCTS, not the words: an absence assertion that trips over its own
    # rationale teaches nothing.
    ok("the FE READS that verdict and re-derives nothing",
       "if (bucket === 'needs_you') return 'awaiting_human'" in common
       and "const GATE_PHASES" not in common   # NOT `GATED_PHASES`, a live export for `atGate`
       and "GATE_PHASES.has(" not in common)
    dash = src("web/frontend/src/features/dev/DevDashboard.tsx")
    ok("the stat row and the deputy strip count by that SAME verdict",
       "primaryStatus(it, buckets[it.id]) === want" in dash
       and "primaryStatus(w, buckets[w.id]) === 'awaiting_human'" in dash)

    # Compaction triggers at run START and nowhere else; end-of-turn only releases the defer
    # latch.
    runs = src("superme_agent/daemon/services/runs.py")
    ws = src("superme_agent/daemon/routers/ws.py")
    ok("end_run only releases the latch — it evaluates nothing",
       "compaction.note_turn_start(session_id)" in runs
       and "maybe_compact" not in runs and "maybe_compact" not in ws)
    ok("the interactive seam checks BEFORE begin_run takes the lock",
       ws.index("compaction.compact_before_run(") < ws.index('begin_run(ctx, ctx.id, work_item_id, "chat"'))
    gates = src("superme_agent/daemon/services/gates.py")
    ok("the background chain checks at the gate seam, then re-enters",
       "_compact_then_readvance(ctx, context_id, item_id, item)" in gates
       and "maybe_autopilot_advance(context_id, item_id)   # the advance this call deferred" in gates)
    comp = src("superme_agent/daemon/services/compaction.py")
    ok("the trigger is the configured number — no session-floor arithmetic left",
       "SESSION_FLOOR_MARGIN" not in comp and "def note_fill" not in comp
       and "floor_pct" not in comp)
    ok("the verdict captures WHAT survived, not just how much",
       '"summary": summary' in comp and "isCompactSummary" in comp)
    # Column definitions do not mix: a shrink is a compaction metric, not usage.
    ok("a compact run claims no usage it did not measure",
       '"estimated": "compact-boundary"' not in comp
       and 'run_usage = real_usage if (real_usage and sum(' in comp
       and 'run_usage = {"input_tokens": int(meta["preTokens"])' not in comp)

    # Produce, then feed back. The two halves are one mechanism; either alone is inert.
    ok("the thread writes its OWN checkpoint before /compact, with a derived fallback",
       "by_agent = await run_handoff_turn(" in comp
       and "banked = by_agent or (bool(item_id) and bank_precompaction_checkpoint(" in comp)
    skill = Path("superme_agent/harness/plugins/superme-dev/skills/checkpoint/SKILL.md")
    ok("the checkpoint skill exists and says what NOT to write", skill.exists()
       and "## What NOT to write" in (s := skill.read_text(encoding="utf-8")) and "never copy" in s)
    ok("...and asks for exactly the five things no artifact holds",
       all(k in s for k in ("in their own words", "cancelled or superseded", "Dead ends",
                            "now stale", "Answered questions")))
    ok("...and forbids bare done/not-done claims + secrets",
       "unverified" in s and "[REDACTED]" in s)
    runs_src = src("superme_agent/daemon/services/runs.py")
    ok("the read-back resolves THIS thread's checkpoint, not the item's newest",
       "def compacted_checkpoint(" in runs_src
       and "_arts.latest_checkpoint(item_dir, char_cap=1, role=role)" in runs_src)
    ok("...gated on the session's newest finished run being the compaction itself",
       "_spine.session_compacted_pending(session_id)" in runs_src)
    ok("the intake thread's triage conversation is banked before it is deleted (row 5)",
       runs_src.index("bank_auto_checkpoint(ctx, item_id)\n        except Exception:\n"
                      "            log.exception(\"pre-replace checkpoint failed")
       < runs_src.index('_sessions.delete(ctx, prev_session, cause="retired")'))
    ok("the floor is measured, not inferred from an observed fill",
       "measure_context_floor(ctx, model)" in comp)
    ok("a compact run records the fill the next turn starts from",
       "post_pct = ev.ctx_pct" in comp and "ctx_pct=post_pct, session_id=session_id" in comp
       and "post_pct: int | None = None   # bound BEFORE the try" in comp)

    # Session-wide, not item-wide: a general session used to fall through to the CLI's own,
    # banking nothing.
    ok("run_compaction opens a plain session run when there is no item",
       "item_id: str | None, session_id: str" in comp
       and '_spine.start_run(context_id, mode=ctx.mode, feature="compact"' in comp
       and "if item_id:\n            end_run(" in comp)
    ok("the derived fallback is item-only — a general session has no artifacts to derive from",
       "banked = by_agent or (bool(item_id) and bank_precompaction_checkpoint(" in comp)
    ok("the general handoff scopes writes to session-memory/ and names the exact path",
       'write_dir = root / "session-memory"' in comp
       and "kernel_speech.session_checkpoint_trigger(" in comp)
    ok("the general seam checks at run START too, and skips read-only diagnosis",
       'session_kind != "diagnosis"' in ws
       and ws.index("run-start compaction check failed (general session)")
       < ws.index("chat_run_id = None if began_run else _spine.start_run("))
    ok("an unbound turn releases the defer latch, like a bound one",
       ws.count("compaction.note_turn_start(final_session)") == 2)
    # The CLI's autocompact can fire INSIDE a turn, past the run-start check, so the net is a
    # logged event.
    ok("a general session's CLI-side autocompact is at least visible in the trail",
       "async def _pre_compact_general(" in ws and '"cli_initiated": True' in ws
       and 'HookMatcher(hooks=[_pre_compact_general])' in ws)
    ok("the general read-back is its own function — no role scoping, one thread",
       "def compacted_session_memory(" in runs_src
       and "_arts.read_session_memory(ctx.internal_root / ctx.mode, session_id" in runs_src)
    ok("...and the notice it feeds drops the item-artifacts fallback",
       "compacted_session_memory(ctx, turn_resume), has_artifacts=False" in ws)
    # A SHORT session scores low by construction, and a manual run cannot loop.
    ok("a manual compaction never accrues a strike or backs a session off",
       "elif not manual:\n            st.strikes += 1" in comp
       and "and not st.backed_off and not manual:" in comp
       and "pre_pct=None if force else pre, manual=force)" in comp)
    ok("...but an effective one still CLEARS strikes, whoever asked",
       'if verdict["effective"] and (bought_runway or manual):\n            st.strikes = 0' in comp)
    # RUNWAY. `effective` answers whether it shrank, never whether that bought working room, and
    # the two came apart.
    ok("an AUTO compaction that bought no runway counts against the back-off however well it shrank",
       "bought_runway = (st.turns_since_compact is None" in comp
       and "or st.turns_since_compact >= MIN_RUNWAY_TURNS)" in comp
       and "st.turns_since_compact = 0   # this compaction is now the one runway is measured from" in comp)
    ok("...and real turns are what count it, off the same latch that releases the defer",
       "if st.turns_since_compact is not None:\n            st.turns_since_compact += 1" in comp)
    # The trigger floor guards WORKING ROOM, not just the incompressible floor.
    ok("the trigger guard refuses a value that leaves no room to work",
       "TRIGGER_MIN_PCT = 40" in comp and "if pct < TRIGGER_MIN_PCT:" in comp
       and "leaves no working room" in comp)
    ok("...and the FE reads that same minimum instead of deriving its own",
       "min={cfg.min_pct}" in src("web/frontend/src/features/config/sections/General.tsx"))
    # `post_pct` came off the /compact Result and was ALWAYS None (a compact turn reports no usage).
    ok("post-compaction fill is DERIVED from measured tokens, not left None",
       "if post_pct is None and window and verdict.get(\"post_tokens\"):" in comp
       and "round(verdict[\"post_tokens\"] / window * 100)" in comp)
    ok("manual /compact is the SAME path with the threshold bypassed, not a second mechanism",
       'manual_compact = prompt.strip().lower() == "/compact"' in ws
       and "force=manual_compact" in ws and "def due(session_id: str | None, kind: str | None, *,"
       " force: bool = False)" in comp)
    ok("...and it never falls through to the CLI as a second compaction",
       "await send(result_frame(_compact_reply(compact_verdict)))" in ws)
    ok("session memory is one agent-written file per session — no writer, no spine pointer column",
       "def session_memory_path(" in (arts := src("superme_agent/core/artifacts.py"))
       and "def write_session_memory(" not in arts
       and "session_memory" not in src("superme_agent/core/spine.py"))
    # One vocabulary: headings, parameters and the worked example share tokens. Where prose and
    # example disagree, the example wins.
    ok("...and a general thread is told to use the same field names as the example",
       "`working_on`,\n  `remaining`, `decisions`, `notes`" in s
       and "like the worked example" in s)
    # The example governs output more than the prose does, so a general thread needs one of its
    # own.
    ok("both thread shapes have a worked example, sharing one set of field names",
       "### A build thread mid-cycle" in s and "### A general thread — no work-item" in s
       and s.count("# working_on:") == 2 and s.count("# decisions:") == 2)
    ok("...and the absent-artifact boilerplate is named and banned",
       "Do not spend lines saying which artifacts are absent" in s
       and "no line saying there is no work-item" in s)
    # `plan.md` is the record for an ITEM; a general thread's decisions have homes of their own.
    flat = " ".join(s.split())   # the skill hard-wraps; match on prose, not line breaks
    ok("a decision with no home is routed, not just flagged",
       "Name what the anchor docs will owe" in flat and "project-prd / architecture / capabilities" in flat
       and "create_inbox_item" in flat)
    ok("a dead end covers a direction ruled out in conversation, not only one attempted",
       "Settled — do not re-open" in flat)


def test_plan_readonly_at_review(tmp: Path) -> None:
    print("plan.md write-deny — the file layer, where the authority actually is")
    from superme_agent.core.permissions import PLAN_READONLY_NUDGE, build_can_use_tool
    item_dir = tmp / "wi" / "it7"
    (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    plan = item_dir / "artifacts" / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    asked: list[str] = []

    async def approve(tool_name, tool_input):     # stands in for the human surface
        asked.append(tool_name)
        return True

    guard = build_can_use_tool(approve, write_boundary=[item_dir],
                               protected_paths=[plan], protected_nudge=PLAN_READONLY_NUDGE)
    res = asyncio.run(guard("Edit", {"file_path": str(plan)}, None))
    ok("an Edit on plan.md is DENIED — the tool guard alone left this path open",
       type(res).__name__ == "PermissionResultDeny")
    # Never a permission card: approving one grants the wholesale rewrite a surgical revise
    # prevents.
    ok("...and the owner is never asked — hard deny, before the boundary allow and before approve",
       not asked and "report_completion" in getattr(res, "message", ""))
    other = asyncio.run(guard("Edit", {"file_path": str(item_dir / "reports" / "report-review.md")},
                              None))
    ok("the rest of the item folder still auto-allows (review writes its own report)",
       type(other).__name__ == "PermissionResultAllow")
    # Scope check: build ticks the checkboxes, so a phase-agnostic deny would stop the one
    # progress signal. The carve-out is review-only.
    unguarded = build_can_use_tool(approve, write_boundary=[item_dir])
    ok("with no protected path (build/plan turns) plan.md writes still pass",
       type(asyncio.run(unguarded("Edit", {"file_path": str(plan)}, None))).__name__
       == "PermissionResultAllow")
    ws = src("superme_agent/daemon/routers/ws.py")
    ok("ws arms it at review only",
       'if str(item.get("phase")) == "review":' in ws
       and 'protected_paths = [item_dir / "artifacts" / "plan.md"]' in ws)


def test_completion_card() -> None:
    print("the closing card — one structured source, rendered not reshaped")
    tl = src("web/frontend/src/features/chat/TimelineView.tsx")
    ok("the card comes from the run.report event's `user` half, not from parsed prose",
       "run.report" in tl and "m.user ?? {}" in tl and "ReportCard" in tl)
    ok("it lays out exactly what `user` carries — summary, next, questions",
       "r.summary" in tl and "r.next" in tl and "r.questions" in tl)
    ok("cards interleave by time, so each one closes ITS run",
       "timed[ri].ts! <= b.ts" in tl and "...reports.map" in tl)
    # The card IS the run's last word. Cut the trail at the call, never by timestamp.
    ok("nothing a background run says after its own ending reaches the channel",
       "report_completion')" in tl and "run.feature !== 'chat') ended = true" in tl
       and "if (ended) continue" in tl)
    ml = src("web/frontend/src/features/chat/MessageList.tsx")
    ok("the fence-era parser is gone — no regex, no splitter, no second card",
       "completion-report" not in ml and "splitReport" not in ml
       and "CompletionReport" not in ml)


def test_chat_can_report() -> None:
    print("ws — the review CONVERSATION can reach the one way back")
    ws_src = src("superme_agent/daemon/routers/ws.py")
    ok("the run-report pen is mounted on every bound work-item turn, not just a parked grill",
       'if work_item_id and ctx.mode == "dev":' in ws_src
       and ws_src.index('make_run_report_server')
       < ws_src.index("end_run(ctx, ctx.id, work_item_id"))
    ok("a reported `revise` is honoured ONLY at review (elsewhere it is just a logged report)",
       'outcome == "revise" and str((item or {}).get("phase")) != "review"' in ws_src)
    # One reader for interactive and kernel-fired endings alike. Pinned as the seam, not as one
    # call's spelling.
    ok("the interactive ending goes through the shared completion reader",
       "read_completion(ctx.id, work_item_id, grill_sink" in ws_src)
    ok("the report's own words ride the routing as `summary`",
       'summary=str((report or {}).get("summary") or "")' in ws_src)


def test_contracts() -> None:
    print("registry + policy + skill contracts")
    from superme_agent.harness.policy import SAFE_TOOLS
    from superme_agent.harness.tools.dev_tools import DEV_TOOLS, ITEM_DEV_TOOLS
    ok("revise_plan registered in the item set",
       any(t.name == "revise_plan" for t in ITEM_DEV_TOOLS)
       and any(t.name == "revise_plan" for t in DEV_TOOLS))
    ok("auto-allowed (prompting here pushes the agent toward the whole-file rewrite)",
       "mcp__dev__revise_plan" in SAFE_TOOLS)
    ok("the second way back is GONE — tool, policy entry, and scheduler",
       not any("route_review_feedback" in t.name for t in DEV_TOOLS)
       and not any("route_review_feedback" in t for t in SAFE_TOOLS)
       and not hasattr(__import__("superme_agent.daemon.services.loop",
                                  fromlist=["x"]), "schedule_review_plan"))
    # The review CONVERSATION's contract is the PREAMBLE's: an interactive turn never loads the
    # phase skill, so words there change nothing.
    from superme_agent.core.kernel_speech import work_item_preamble
    convo = " ".join(work_item_preamble(
        "it7", {"title": "t", "phase": "review", "kind": "implementation"}, "/tmp/it7",
        interactive=True).split())
    ok("the review conversation carries the routing step (verbatim, re-vetted, one way back)",
       "report_completion(machine.outcome='revise')" in convo and "VERBATIM" in convo
       and "re-vetted" in convo)
    ok("...and that this chat fixes nothing — new scope becomes an inbox item",
       "Do NOT start fixing in this session" in convo and "create_inbox_item" in convo)
    ok("...and frames the shared 3-speaker chat (owner + you + deputy)",
       "shared terminal" in convo and "deputy" in convo)
    # Naming a flaw is not commissioning a fix, so the precondition is stated as something
    # checkable in the transcript.
    ok("...gated on the owner's word, not the agent's reading, with the ask-back owed",
       "their instruction to change the plan, or your offer and their yes" in convo
       and "what they have NOT addressed" in convo
       and "silence on a point is not agreement" in convo)
    # The SKILL is the review-ENTRY RUN's procedure: no git in it, and one skill for both kinds.
    skill = src("superme_agent/harness/plugins/superme-dev/skills/review/SKILL.md")
    flat = " ".join(skill.split())
    ok("the review skill is the ENTRY RUN: read → name the doc debt → write report-review.md → report",
       "report-review.md" in flat and "the CLOSE run writes them" in flat
       and "stage_knowledge_delta" not in flat
       and "report_completion" in flat and "build-vet-<n>.md" in flat)
    ok("...and it is read-only on the tree — inspect git, change nothing (the merge act owns that)",
       "Read-only on the tree" in flat
       and "change nothing: no sync, no commit, no merge" in flat)
    ok("...and it fixes nothing — no code, no plan edit, in the run OR later in the session",
       "no code, no plan edit, here or later in this session" in flat)
    # Review names the doc debt and holds no tool that could ask permission.
    ok("review reports what the docs will owe rather than requesting it",
       "Name it; never ask permission for it" in flat
       and "request_authorization" not in flat)
    ok("...and the naming carries a LABELLED example", "**Good example**" in flat)
    # The owner's report is KIND-NEUTRAL: the questions a person asks before approving do not
    # change with the workflow.
    # The template is the skill's own package again, so the skill NAMES it: one kind-neutral
    # shape, cited once, and no per-kind variant beside it.
    ok("...and one owner report template serves every kind, with no second review skill",
       "templates/report-review-template.md" in flat
       and Path("superme_agent/harness/plugins/superme-dev/skills/review/templates/"
                "report-review-template.md").is_file()
       and not Path("superme_agent/harness/plugins/superme-dev/skills/review/templates/"
                    "report-review-research-template.md").exists()
       and not Path("superme_agent/harness/plugins/superme-dev/skills/"
                    "research-report").exists())
    ok("...while the RECORD keeps its per-kind shape",
       all(Path("superme_agent/harness/plugins/superme-dev/skills/review/templates/"
                + f).is_file() for f in ("review-template.md", "review-research-template.md")))
    plan_skill = " ".join((src("superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md") + "\n" + src("superme_agent/harness/plugins/superme-dev/skills/plan/references/revising-a-plan.md")).split())
    ok("plan skill teaches the per-change scope ladder and the proportionality REFUSAL",
       "revise_plan" in plan_skill and "resume" in plan_skill and "targeted" in plan_skill
       and "redesign" in plan_skill and "Never rewrite `plan.md` by hand" in plan_skill
       and "own** scope" in plan_skill and "The proportionality rule is a refusal, not advice" in plan_skill
       and "directive" in plan_skill and "still_in_force" in plan_skill)
    build_skill = " ".join(src("superme_agent/harness/plugins/superme-dev/skills/build/SKILL.md").split())
    ok("build skill reads the newest block + still-in-force, and undoes FORWARD",
       "## Revision r<n>" in build_skill and "undone FORWARD" in build_skill
       and "never a reset" in build_skill and "still in force" in build_skill
       and "only task authority" in build_skill)
    vet_skill = " ".join(src("superme_agent/harness/plugins/superme-dev/skills/vet/SKILL.md").split())
    ok("vet skill knows the verification plan is LIVE and may have been revised mid-item",
       "It is LIVE" in vet_skill and "revision mid-item" in vet_skill)
    # A no-op cycle that leaves raw fill slots is unreadable as "nothing to do" versus "gave up".
    ok("a nothing-to-build cycle still answers, instead of leaving the slots",
       "A cycle with nothing to build still fills them" in build_skill
       and "reads as a build that gave up" in build_skill)
    ok("close skill exists (the auto-close run drives it)",
       Path("superme_agent/harness/plugins/superme-dev/skills/close/SKILL.md").is_file())


def test_review_entry_run() -> None:
    """Review HAS a runner, and both doors into review use the same one."""
    print("slice 4a — the review-ENTRY run, one skill, both doors")
    from superme_agent.core import kernel_speech
    from superme_agent.core.vocab import token_taxonomy
    from superme_agent.daemon.services import runs as runs_svc
    gates_src = src("superme_agent/daemon/services/gates.py")
    loop_src = src("superme_agent/daemon/services/loop.py")
    ok("a shared firer exists — one implementation, not one per door",
       callable(getattr(runs_svc, "fire_review_entry", None)))
    ok("advance_item's review branch goes through it",
       "auto_started = fire_review_entry(context_id, item_id, spine)" in gates_src)
    ok("...and the LOOP's vet→review hop does too (the path items normally take)",
       'if d["action"] == "review":' in loop_src and "fire_review_entry" in loop_src)
    ok("review is NOT in the generic auto_skill map — one dispatch site, no second copy",
       '{"plan": "plan", "investigate": "investigate"}' in gates_src)
    ok("review no longer rests at awaiting_human on entry (a run follows it now)",
       'if nxt == "review":\n        dev.set_work_item_status(dev_root, item_id, "awaiting_human")'
       not in gates_src)
    ok("the deputy is not double-dispatched when the entry run started",
       'elif nxt == "review" and autopiloted and not auto_started:' in gates_src)
    ok("...and it judges nothing while ANY run holds the item's lock",
       "if spine.is_item_running(context_id, item_id):" in gates_src)
    ok("the runner is named for what it does, not for the one kind that used to call it",
       callable(getattr(runs_svc, "run_background_item_skill", None))
       and not hasattr(runs_svc, "_run_background_research"))
    # One skill for both kinds: the per-kind override is gone, and so is the parallel skill.
    ok("no per-kind review override remains",
       kernel_speech._KIND_PHASE_CONTRACTS == {})
    for kind in ("implementation", "research"):
        ok(f"...so {kind}'s review phase resolves to the SAME skill",
           kernel_speech.phase_contract(kind, "review").get("skill") == "review")
    # The phase contract and the skill must agree about git, or the run gets opposite
    # instructions.
    bg = kernel_speech.work_item_preamble(
        "it1", {"title": "T", "phase": "review", "kind": "implementation",
                "git_worktree": "/wt/x", "git_base": "abc123"}, "/i", interactive=False)
    ok("the review phase contract points at the review skill and permits READING git",
       "superme-dev:review" in bg and "read git, change none of it" in bg)
    ok("...and the branch base rides the preamble, since the report's diff stat needs it",
       "Branch base: `abc123`" in bg)
    ok("`review` is a work-item token bucket (it can appear in run rows now)",
       token_taxonomy.category_for("review") == "workitem")
    ok("...and the absorbed feature stays mapped, so old run rows still aggregate",
       token_taxonomy.category_for("research-report") == "workitem")
    tmpl = Path("superme_agent/harness/plugins/superme-dev/skills/review/templates")
    ok("the review skill owns the owner report AND both agent-facing records, old home gone",
       (tmpl / "report-review-template.md").is_file()
       and (tmpl / "review-template.md").is_file()
       and (tmpl / "review-research-template.md").is_file()
       and not (tmpl / "report-review-research-template.md").exists()
       and not Path("superme_agent/harness/plugins/superme-dev/skills/"
                    "research-report").exists())
    # Four QUESTIONS, not a field sheet: what a machine parses lives in the agent-facing record.
    owner = (tmpl / "report-review-template.md").read_text(encoding="utf-8")
    ok("the owner's report asks the four questions, and opens with the Summary line",
       "**Summary:**" in owner
       and all(f"## {s}" in owner for s in ("What you're approving", "What to push back on",
                                            "How much to trust it",
                                            "Where this leaves the project")))
    ok("...and carries no machine field and no diff section",
       not any(k in owner for k in ("**Delivered:**", "## Proposed work", "Owner's decision",
                                    "## Changed since", "Recommendation:")))
    ok("...a re-review answers the objection FIRST instead of appending a delta",
       "## What you asked for" in owner)
    rec = (tmpl / "review-template.md").read_text(encoding="utf-8")
    ok("the RECORD carries what the machines parse",
       "**Delivered:**" in rec and "## Revision rounds" in rec)
    ok("...and its slots are real fill slots the report check can see",
       "<fill:" in owner and "<fill:" in rec)


def test_repo_knobs(tmp: Path) -> None:
    """The repo's two git knobs, read live at every consumption point.

    A configured anchor that does not exist RAISES rather than falling back."""
    import subprocess
    from superme_agent.core import git_layer as G
    from superme_agent.core.spine import (REVIEW_MODES, REVIEW_MODE_DEFAULT, RepoConfig,
                                          SystemSpine, load_repos)

    ok("review modes are exactly fast|strict", REVIEW_MODES == ("fast", "strict"))
    ok("every repo defaults to fast", REVIEW_MODE_DEFAULT == "fast")
    rc = RepoConfig(id="x", label="X", cwd=tmp)
    ok("a fresh RepoConfig is fast with a derived anchor",
       rc.review_mode == "fast" and rc.anchor_branch is None)
    ok("an unknown mode falls back rather than crashing the registry",
       RepoConfig(id="x", label="X", cwd=tmp, review_mode="yolo").review_mode == "fast")
    ok("defaults are stored as ABSENCE (one representation for 'unset')",
       "review_mode" not in rc.to_dict() and "anchor_branch" not in rc.to_dict())
    ok("a non-default IS written",
       RepoConfig(id="x", label="X", cwd=tmp, review_mode="strict",
                  anchor_branch="develop").to_dict()["review_mode"] == "strict")

    # --- the line-level repos.yaml updater -------------------------------------------
    cfg = tmp / "repos.yaml"
    cfg.write_text("# a header comment that must survive\n\nrepos:\n"
                   "  alpha:\n    label: Alpha\n    cwd: \".\"   # trailing comment\n"
                   "    layer: local\n"
                   "  beta:\n    label: Beta\n    cwd: /tmp\n    layer: local\n", encoding="utf-8")
    before = cfg.read_text(encoding="utf-8")
    spine = SystemSpine.__new__(SystemSpine)
    spine._repos_config_path = cfg
    got = spine.update_repo("alpha", review_mode="strict", anchor_branch="develop")
    ok("update_repo returns the reloaded repo",
       got.review_mode == "strict" and got.anchor_branch == "develop")
    after = cfg.read_text(encoding="utf-8")
    ok("the header comment survives", "# a header comment that must survive" in after)
    ok("an inline comment on an untouched line survives", "# trailing comment" in after)
    ok("the OTHER entry is untouched", "  beta:\n    label: Beta" in after)
    ok("only the two knob lines were added",
       [ln for ln in after.splitlines() if ln not in before.splitlines()]
       == ["    review_mode: strict", "    anchor_branch: develop"])
    spine.update_repo("alpha", review_mode="fast", anchor_branch="")
    ok("setting both back to their defaults restores the file byte-for-byte",
       cfg.read_text(encoding="utf-8") == before)
    ok("a second repo's knobs don't leak", load_repos(cfg)["beta"].review_mode == "fast")
    for bad, why in ((dict(review_mode="yolo"), "an invalid mode"),
                     (dict(cwd="/elsewhere"), "a non-editable field"),
                     (dict(review_mode="strict"), "an unknown repo")):
        try:
            spine.update_repo("alpha" if "cwd" in bad or "yolo" in str(bad) else "nope", **bad)
            ok(f"{why} is refused", False)
        except ValueError:
            ok(f"{why} is refused", True)

    # --- resolve_anchor: the refusal, not a fallback ---------------------------------
    repo = tmp / "anchored"
    repo.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    run("init", "-b", "main")
    run("config", "user.email", "t@t"); run("config", "user.name", "t")
    (repo / "f.txt").write_text("x", encoding="utf-8")
    run("add", "."); run("commit", "-m", "init")
    ok("no override → the repo's own default branch", G.resolve_anchor(repo) == "main")
    run("branch", "develop")
    ok("a configured branch that EXISTS is used", G.resolve_anchor(repo, "develop") == "develop")
    try:
        G.resolve_anchor(repo, "ghost")
        ok("a configured branch that is MISSING raises (never falls back)", False)
    except G.GitError as e:
        ok("a configured branch that is MISSING raises (never falls back)",
           "ghost" in str(e) and "will not fall back" in str(e))

    # A READ probe reports a broken anchor instead of raising, so the tab shows a reason, not a
    # blank.
    h = G.worktree_health(repo, "rid", "itm", "main", trunk="ghost")
    ok("health reports a broken anchor rather than crashing the Git tab",
       h["ok"] is False and "ghost" in h["reason"])
    h = G.worktree_health(repo, "rid", "itm", "main", trunk="develop")
    ok("health measures against the ANCHOR, not the default branch", h["trunk"] == "develop")

    # --- every git site takes the anchor ---------------------------------------------
    import inspect
    for fn, param in ((G.create_worktree, "base"), (G.worktree_health, "trunk"),
                      (G.merge_to_main, "target"), (G.sync_from_main, "target"),
                      (G.revert_merge, "target")):
        ok(f"{fn.__name__} accepts the anchor as `{param}`",
           param in inspect.signature(fn).parameters)
    git_layer_src = src("superme_agent/core/git_layer.py")
    ok("no git site derives the default branch behind the anchor's back",
       "or default_branch(repo_dir)" not in git_layer_src)

    # --- the daemon's single reader --------------------------------------------------
    from superme_agent.daemon.services import git_ops
    class _Ctx:  id = "alpha"
    class _Spine:
        def repo(self, rid): return load_repos(cfg).get(rid)
    ok("repo_anchor reads the live config", git_ops.repo_anchor(_Ctx(), _Spine()) is None)
    ok("repo_review_mode reads the live config",
       git_ops.repo_review_mode(_Ctx(), _Spine()) == "fast")
    spine.update_repo("alpha", review_mode="strict")
    ok("a mode flip is visible WITHOUT a restart (items already at review are governed by it)",
       git_ops.repo_review_mode(_Ctx(), _Spine()) == "strict")
    class _NoRepo:  id = "gone"
    ok("an unregistered context degrades to the defaults, never a crash",
       git_ops.repo_anchor(_NoRepo(), _Spine()) is None
       and git_ops.repo_review_mode(_NoRepo(), _Spine()) == "fast")

    # --- both landing knobs live in the project's settings pane -----------------
    # One rule with two owners.
    ps = src("web/frontend/src/features/config/sections/ProjectSettings.tsx")
    dw = src("web/frontend/src/features/dev/DevWorkspace.tsx")
    ok("the review-mode picker lives in Project - Settings",
       "REVIEW_MODES" in ps and "setRepoGit(repo.id, { review_mode: v })" in ps)
    ok("...and the anchor picker sits beside it, off the repo's real branches",
       "getRepoBranches" in ps and "setRepoGit(repo.id, { anchor_branch: v })" in ps)
    ok("neither knob is ALSO on the workspace header — one question, one place to answer it",
       "setRepoGit" not in dw)
    ok("the picker labels are bare words; the meaning moved to the row hint",
       "{ value: 'fast', label: 'Fast' }" in ps
       and "Fast merges an item when you approve it." in ps)


def test_the_checkers_run_on_their_own_tier() -> None:
    """Vet and the deputy do NOT inherit the model the work runs on.

    A judge that rises with what it judges is not a second opinion."""
    print("vet and the deputy resolve on their own tier, never the project's")
    import tempfile
    from superme_agent.core.spine import SystemSpine
    with tempfile.TemporaryDirectory() as td:
        sp = SystemSpine(db_path=Path(td) / "s.db")
        sp.set_model_override("r", "opus")          # the PROJECT is on opus
        sp.set_effort_override("r", "high")
        floor_m, floor_e = sp.effective_system_model(), sp.effective_system_effort()
        ok("the project's own tier still resolves through the project chain",
           sp.effective_model("r") == "opus" and sp.effective_effort("r") == "high")
        ok("...and vet does NOT inherit it — unset means the floor",
           sp.role_model("r", "vet") == floor_m and sp.role_effort("r", "vet") == floor_e)
        ok("...nor does the deputy", sp.deputy_params() == (floor_m, floor_e))

        sp.set_model_override("r", "haiku", "vet")   # the vet role, set on its own row
        sp.set_effort_override("r", "low", "vet")
        ok("a vet tier is stored per ROLE, leaving the project's row alone",
           sp.role_model("r", "vet") == "haiku" and sp.effective_model("r") == "opus")
        ok("...and the roster still reports the project's tier, not the role's",
           sp.all_model_overrides() == {"r": "opus"})

        sp.set_deputy_model("opus")
        ok("the deputy's tier is SYSTEM-scope — one judge, one answer",
           sp.deputy_params()[0] == "opus")
        ok("an item may still name its own for either role",
           sp.role_model("r", "vet", item_model="sonnet") == "sonnet"
           and sp.deputy_params(item_model="haiku")[0] == "haiku")

        # The runners must actually ASK for those chains — a resolver nothing calls is not a rule.
        loop_src = src("superme_agent/daemon/services/loop.py")
        dep_src = src("superme_agent/daemon/services/deputy.py")
        ok("the vet run resolves through the vet chain",
           '_spine.role_model(context_id, "vet", item_model=item.get("vet_model"))' in loop_src
           and "model, effort = _resolve_vet_params(context_id, item)" in loop_src)
        ok("...and the build run still resolves through the item's own",
           "model, effort = _resolve_run_params(context_id, item)" in loop_src)
        ok("the deputy resolves through its own",
           'item_model=item.get("deputy_model")' in dep_src
           and "effective_model" not in dep_src)


def test_prompt_xray_covers_every_speaker() -> None:
    """The X-ray's two gaps: a speaker with no capture site, and a capture holding prose only.

    Two runs on identical words look identical and behaved differently."""
    from superme_agent.daemon.services.runs import turn_surface

    dep = src("superme_agent/daemon/services/deputy.py")
    ok("the deputy captures its input like every other run",
       "capture_run_input(" in dep and "is_prompt_extraction(item)" in dep)
    ok("...tagged by the gate it judged, so three deputy runs stay distinguishable",
       'phase=f"deputy:{gate}"' in dep)

    s = turn_surface(model="sonnet", effort="high", mcp=["dev", "run"],
                     write_boundary=[Path("/wt")], sandbox_writes=[Path("/wt")], resumes=True)
    ok("the surface records the model it ran on", s["model"] == "sonnet" and s["effort"] == "high")
    ok("...the tools it carried", s["mcp"] == ["dev", "run"])
    ok("...where it could write, as plain strings",
       s["sandbox_writes"] == [str(Path("/wt"))])
    ok("...and whether a transcript it does NOT hold is also in play",
       s["resumes"] is True)
    ok("a read-only turn says so rather than leaving it implied",
       turn_surface(read_only=True)["read_only"] is True)

    # DERIVED, never restated: a capture that re-declares the surface can describe permissions the
    # turn never got.
    for name in ("loop", "runs", "deputy"):
        svc_src = src(f"superme_agent/daemon/services/{name}.py")
        ok(f"{name}.py records the surface", "surface=surface_from_turn(" in svc_src)
        ok(f"...and {name}.py restates none of it", "surface=turn_surface(" not in svc_src)
        if name != "runs":
            ok(f"...sending the same dict it snapshotted, in {name}.py",
               "turn.stream(_agent" in svc_src and "**turn_kwargs)" in svc_src)
    prev = src("superme_agent/daemon/services/input_preview.py")
    # The invariant is that the surface gets its OWN section, not which number it carries.
    ok("the inspector renders it as its own channel", "Turn surface" in prev)
    ok("...and OMITS the block for rows captured before it existed, rather than printing dashes",
       "if not surface:\n        return \"\"" in prev)

    # A probe tears down at CLEARANCE: a clean close never rests at the gate, so it would leak.
    clr = src("superme_agent/daemon/services/clearance.py")
    ok("a completed probe tears itself down at the item's terminal moment",
       "is_prompt_extraction(item)" in clr and "px.teardown(" in clr)
    gts = src("superme_agent/daemon/services/gates.py")
    ok("...and the close-gate hook still covers a probe that PARKS there instead",
       "px.teardown(context_id, item_id, reason=\"probe reached close\")" in gts)
    pxs = src("superme_agent/daemon/services/prompt_extraction.py")
    ok("teardown is what closes the probe state, so the tab stops saying 'running' forever",
       '"status": "done"' in pxs and '"finished_at": _now()' in pxs)


def test_repo_activity_scope(tmp: Path) -> None:
    """The Activity tab's ONE read: dev-native rows plus the item rows, in one order."""
    from superme_agent.core.dev_store import DevStore, REPO_MILESTONE_KINDS

    st = DevStore(tmp / "act.db")
    st.log_event("r", "inbox.add", "captured")                          # dev-native
    st.log_event("r", "git.pr", "PR opened", item_id="i1")              # milestone
    st.log_event("r", "item.complete", "done", item_id="i1")            # milestone
    st.log_event("r", "phase.advance", "vet → review", item_id="i1")    # per-item trace
    st.log_event("r", "run.report", "success: …", item_id="i1")         # per-item trace
    st.log_event("r", "vet.start", "Started vet run", item_id="i2")     # per-item trace

    kinds = {e["kind"] for e in st.list_events("r", scope="repo")}
    ok("the repo view carries dev-native rows", "inbox.add" in kinds)
    ok("...and the item-scoped MILESTONES", {"git.pr", "item.complete"} <= kinds)
    ok("...and none of the per-item trace",
       not ({"phase.advance", "run.report", "vet.start"} & kinds))
    ok("the milestone set is the one the surface documents",
       set(REPO_MILESTONE_KINDS)
       == {"git.pr", "git.merge", "git.worktree", "inbox.push", "item.complete"})
    ok("the stored scopes still read as themselves — `repo` is a VIEW, not a scope value",
       {e["kind"] for e in st.list_events("r", scope="dev")} == {"inbox.add"}
       and len(st.list_events("r", scope="item")) == 5)
    ok("...and an unfiltered read is still everything", len(st.list_events("r")) == 6)

    act = src("web/frontend/src/features/dev/ActivityLog.tsx")
    ok("the Activity tab asks for that view and offers no scope chips",
       "scope: 'repo'" in act and "TabBar" not in act and "SCOPES" not in act)


def test_branch_list(tmp: Path) -> None:
    """`list_branches` — the anchor picker's option set.

    The anchor REFUSES a branch that does not exist, so the picker must offer only real ones."""
    import os
    import subprocess
    from superme_agent.core import git_layer as G

    os.environ["SUPERME_WORKTREES_HOME"] = str(tmp / "wt")
    repo = tmp / "branchy"
    repo.mkdir()

    def g(*a):
        return subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, encoding="utf-8")

    ok("a non-repo answers with an empty list, never a raise", G.list_branches(repo) == [])
    g("init", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "a.txt").write_text("1", encoding="utf-8")
    g("add", "."); g("commit", "-m", "init")
    g("branch", "develop")
    G.create_worktree(repo, "rid", "itm001", "Some work")

    got = G.list_branches(repo)
    ok("real branches are offered", set(got) == {"main", "develop"})
    ok("work-item branches are NOT — they are transient, one per item",
       not any(b.startswith(f"{G.BRANCH_PREFIX}/") for b in got))
    ok("the prefix has ONE definition, so the writer and this reader cannot drift",
       G.branch_name("itm001", "Some work").startswith(f"{G.BRANCH_PREFIX}/"))


def test_merge_act(tmp: Path) -> None:
    """The merge act: squash into the anchor, and the freshness rule that owns it.

    A squash is not an ancestor, so ancestry reads a merged item as unmerged."""
    import os
    import subprocess
    from superme_agent.core import git_layer as G
    from superme_agent.daemon.services.git_ops import squash_message

    os.environ["SUPERME_WORKTREES_HOME"] = str(tmp / "worktrees")
    repo = tmp / "anchor-repo"
    repo.mkdir()

    def g(*a, cwd=repo):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8")

    g("init", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "a.txt").write_text("1", encoding="utf-8")
    g("add", "."); g("commit", "-m", "init")

    rec = G.create_worktree(repo, "rid", "itm", "Tally by category")
    wt = Path(rec["worktree"])
    for i in (1, 2, 3):                       # three per-task commits, the shape review reads
        (wt / f"f{i}.txt").write_text(str(i), encoding="utf-8")
        g("add", ".", cwd=wt)
        # Task id in the TRAILER, never the subject; the gate a fresh worktree installs rejects
        # the old way.
        g("commit", "-m", f"Add f{i}\n\nSuperMe-Task: t{i}", cwd=wt)

    res = G.merge_to_main(repo, "rid", "itm", rec["branch"],
                          message="feat: Tally by category\n\nAdds per-category totals.")
    ok("the merge lands", res["merged"] is True)
    log = [ln for ln in g("log", "--oneline", "main").stdout.splitlines()]
    ok("ONE commit on the anchor, not three plus a merge", len(log) == 2)
    ok("...carrying the composed message",
       g("log", "-1", "--format=%B").stdout.startswith("feat: Tally by category\n\nAdds per-category"))
    ok("the squash commit has ONE parent (it is not a merge commit)",
       len(g("rev-list", "--parents", "-1", "HEAD").stdout.split()) == 2)
    ok("the branch is KEPT — per-task granularity survives as trace",
       G.branch_exists(repo, rec["branch"])
       and len(g("log", "--oneline", rec["branch"]).stdout.splitlines()) == 4)

    ok("ANCESTRY now reads the merged item as unmerged — the trap this slice defuses",
       g("merge-base", "--is-ancestor", rec["branch"], "main").returncode != 0)
    ok("the recorded sha reads it correctly",
       G._is_merged(repo, rec["branch"], "main", res["merge_commit"]) is True)
    ok("a recorded sha that no longer resolves reads as NOT merged (never strand an item)",
       G._is_merged(repo, "nosuch", "main", "0" * 40) is False)
    ok("worktree_health.merged is right WITH the recorded sha",
       G.worktree_health(repo, "rid", "itm", rec["branch"],
                         merge_commit=res["merge_commit"])["merged"] is True)
    ok("...and wrong without it — which is exactly why every caller must pass it",
       G.worktree_health(repo, "rid", "itm", rec["branch"])["merged"] is False)
    ok("never-merge-twice holds on the recorded sha",
       G.merge_to_main(repo, "rid", "itm", rec["branch"],
                       merged_commit=res["merge_commit"])["already_merged"] is True)
    ok("...and STILL holds with the sha lost (the crash-between-merge-and-record retry): "
       "a squash that stages nothing is the no-op it looks like",
       G.merge_to_main(repo, "rid", "itm", rec["branch"])["already_merged"] is True)
    ok("a retry leaves NO stray backup ref (nothing happened, nothing to restore to)",
       len(g("for-each-ref", "refs/backup/").stdout.splitlines()) == 1)
    rv = G.revert_merge(repo, res["backup_ref"])
    ok("revert works off a squash (its single parent IS the pre-merge head)", rv["reverted"] is True)
    ok("the FIRST backup survived two retries in the same second — unique ref names",
       rv["head"] == g("rev-list", "--max-parents=0", "HEAD").stdout.strip())
    ok("...and the anchor is back", len(g("log", "--oneline", "main").stdout.splitlines()) == 1)

    # --- the commit message is kernel-assembled ---------------------------------------
    idir = tmp / "item-dir"
    # The body comes off the AGENT-facing record: it outlives this workspace, and machines parse
    # it.
    (idir / "artifacts").mkdir(parents=True)
    (idir / "artifacts" / "review.md").write_text(
        "# Review Agent-facing Report\n\n**Delivered:** a --category filter\n\n"
        "## Change inventory\n| surface | change | tasks |\n", encoding="utf-8")
    msg = squash_message({"title": "Tally by category"}, "itm", idir)
    ok("the subject is `<type>: <item title>` when review declared nothing",
       msg.splitlines()[0] == "feat: Tally by category")
    ok("the body is the review RECORD's Delivered line", "a --category filter" in msg)
    (idir / "reports").mkdir(parents=True)
    (idir / "reports" / "report-review.md").write_text(
        "# Review User-facing Report\n\n**Delivered:** the owner's report is not the source\n", encoding="utf-8")
    ok("...and the owner's report is never the source, even when it carries the field",
       "the owner's report is not the source"
       not in squash_message({"title": "Tally by category"}, "itm", idir))
    ok("no SuperMe vocabulary above the trailer block",
       "work-item" not in msg and "item/" not in msg
       and "review" not in msg.split("SuperMe-")[0].lower())
    ok("a missing report still yields a valid subject",
       squash_message({"title": "No report"}, "itm", tmp / "nowhere").startswith("feat: No report"))
    # The record is prose wrapped for READING, so taking only its first line ends the commit mid-
    # sentence.
    wrapped = tmp / "item-wrapped"
    (wrapped / "artifacts").mkdir(parents=True)
    (wrapped / "artifacts" / "review.md").write_text(
        "# Review Agent-facing Report\n\n**Delivered:** `tally list` now defaults to the 20 most recent\n"
        "entries, accepts `--all` to print every one, and keeps `--limit N` as an override.\n\n"
        "**Change inventory:** not a field, just the next bold thing\n", encoding="utf-8")
    wmsg = squash_message({"title": "x"}, "itm", wrapped)
    ok("the WHOLE Delivered paragraph rides the commit, not just its first line",
       "as an override." in wmsg and "20 most recent entries, accepts" in wmsg)
    ok("...and it stops at the paragraph — the next bold field is not swallowed",
       "Key decisions" not in wmsg and "none worth recording" not in wmsg)
    ok("...re-wrapped at 72, so `git log` reads right on an 80-column terminal",
       max(len(ln) for ln in wmsg.splitlines()) <= 72)

    # --- freshness: the four branches --------------------------------------------------
    r2 = tmp / "fresh-repo"
    r2.mkdir()

    def g2(*a, cwd=r2):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8")

    g2("init", "-b", "main"); g2("config", "user.email", "t@t"); g2("config", "user.name", "t")
    # Long enough that edits at each end merge CLEANLY: git is happy, the behaviour may not be.
    lines = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    (r2 / "mine.txt").write_text(lines, encoding="utf-8")
    (r2 / "theirs.txt").write_text("base\n", encoding="utf-8")
    (r2 / "shared.txt").write_text("base\n", encoding="utf-8")
    g2("add", "."); g2("commit", "-m", "init")
    rec2 = G.create_worktree(r2, "rid2", "itm2", "freshness")
    wt2 = Path(rec2["worktree"])
    (wt2 / "mine.txt").write_text(lines.replace("line 20", "line 20 — changed by the item"), encoding="utf-8")
    g2("add", ".", cwd=wt2)
    g2("commit", "-m", "Add mine\n\nSuperMe-Task: t1", cwd=wt2)

    ok("anchor unmoved → merge, no sync attempted",
       G.merge_freshness(r2, wt2, rec2["branch"]) == {"action": "merge"})

    (r2 / "theirs.txt").write_text("theirs\n", encoding="utf-8")   # a sibling lands somewhere this item never touched
    g2("add", "."); g2("commit", "-m", "sibling: theirs")
    out = G.merge_freshness(r2, wt2, rec2["branch"])
    ok("anchor moved with NO path overlap → merge (the cohort converges)", out["action"] == "merge")
    ok("...and the anchor was actually synced in", bool(out.get("synced")))

    (r2 / "mine.txt").write_text(
        lines.replace("line 1\n", "line 1 — changed by a sibling\n", 1), encoding="utf-8")
    g2("add", "."); g2("commit", "-m", "sibling: also mine")
    out = G.merge_freshness(r2, wt2, rec2["branch"])
    ok("anchor moved OVER a file this item changed, CLEANLY → one vet cycle, not a blind merge",
       out["action"] == "revet" and out["paths"] == ["mine.txt"], str(out))
    ok("...and the sync happened, so the re-vet runs against the merged tree", bool(out["synced"]))

    (r2 / "shared.txt").write_text("anchor version\n", encoding="utf-8")
    g2("add", "."); g2("commit", "-m", "sibling: shared")
    (wt2 / "shared.txt").write_text("item version\n", encoding="utf-8")
    g2("add", ".", cwd=wt2)
    g2("commit", "-m", "Touch the shared file\n\nSuperMe-Task: t2", cwd=wt2)
    out = G.merge_freshness(r2, wt2, rec2["branch"])
    ok("a conflicting anchor → PARK and page, never an unwatched auto-resolve",
       out["action"] == "park" and out["conflicts"] == ["shared.txt"], str(out))
    ok("...and the worktree is left clean (the aborted sync unwound itself)",
       not G.check_git_state(wt2)["dirty"])

    # --- the light path is deliberately NOT migrated -----------------------------------
    git_layer_src = src("superme_agent/core/git_layer.py")
    light = git_layer_src.split("def merge_into_parent")[1].split("def sync_from_main")[0]
    ok("merge_into_parent still merges --no-ff", '"merge", "--no-ff", child_branch' in light)
    ok("...so ancestry is still its correct merged-test", "--is-ancestor" in light)
    note = " ".join(l for l in light.splitlines() if l.strip().startswith("#")).lower()
    ok("...and that is stated, so nobody 'fixes' it later",
       "ancestry" in note and "squash" in note)


def test_commit_contract(tmp: Path) -> None:
    """The message is for the project, the trailers are for SuperMe.

    A repository's readers have never heard of this workspace. The mechanical rules bind at the
    tool."""
    import asyncio
    import subprocess
    from superme_agent.core import git_layer as G
    from superme_agent.harness.tools.run_tools import COMMIT_TYPES, _report_completion
    from superme_agent.daemon.services.git_ops import squash_message, declared_commit

    ok("four types, no more", COMMIT_TYPES == ("feat", "fix", "refactor", "chore"))
    ok("no `docs`/`style` — vet can verify neither, so neither is an implementation item",
       "docs" not in COMMIT_TYPES and "style" not in COMMIT_TYPES)

    sink: dict = {}
    tool = _report_completion(completion_sink=sink)

    def report(commit):
        sink.clear()
        return asyncio.run(tool({"machine": {"outcome": "success", **({"commit": commit} if commit
                                                                     else {})},
                                 "user": {"summary": "s", "next": "n"}}))

    good = {"type": "fix", "subject": "Reject empty category names"}
    ok("a valid spec is accepted", report(good).get("is_error") is not True)
    ok("...and reaches the sink under machine.commit",
       sink["report"]["machine"]["commit"] == good)
    ok("the field is OPTIONAL — research declares none", report(None).get("is_error") is not True)
    ok("...and then no commit key is stored", "commit" not in sink["report"]["machine"])

    for spec, why in (
        ({"type": "docs", "subject": "Update the readme"}, "a type outside the four"),
        ({"type": "feat", "subject": ""}, "an empty subject"),
        ({"type": "feat", "subject": "feat: Add the flag"}, "a subject repeating its own type"),
        ({"type": "feat", "subject": "A" * 51}, "a subject over 50 characters"),
        ({"type": "feat", "subject": "Add the flag."}, "a trailing period"),
        ({"type": "feat", "subject": "add the flag"}, "a lowercase start"),
        ("feat: whatever", "a bare string instead of the object"),
    ):
        r = report(spec)
        ok(f"{why} is refused", r.get("is_error") is True, str(r))

    # --- the assembler ----------------------------------------------------------------
    msg = G.compose_commit(
        "fix: Reject empty category names",
        "The tally crashed on an empty --category value because the row filter assumed a "
        "non-empty key. It validates at parse time now.",
        {"SuperMe-Item": "4f2a1b9c0d3e", "SuperMe-Parent": ""})
    lines = msg.splitlines()
    ok("subject first, then a blank line", lines[1] == "")
    ok("the body wraps at 72 for `git log` on an 80-column terminal",
       max(len(x) for x in lines) <= 72)
    ok("the trailers are the FINAL block", lines[-1] == "SuperMe-Item: 4f2a1b9c0d3e")
    ok("...preceded by a blank line, so git recognizes them", lines[-2] == "")
    ok("an absent fact is OMITTED, never written as an empty value", "SuperMe-Parent" not in msg)
    ok("nothing workspace-shaped above the trailer block",
       "4f2a1b9c0d3e" not in "\n".join(lines[:-1]))

    # git itself must agree these are trailers — not our own idea of the format
    repo = tmp / "trailer-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True)
    got = subprocess.run(["git", "interpret-trailers", "--parse"], cwd=repo, input=msg,
                         capture_output=True, text=True, encoding="utf-8")
    ok("git interpret-trailers reads them back", got.stdout.strip() == "SuperMe-Item: 4f2a1b9c0d3e")

    # --- the kernel assembles from the DECLARATION ------------------------------------
    idir = tmp / "commit-item"
    (idir / "artifacts").mkdir(parents=True)
    (idir / "artifacts" / "review.md").write_text(
        "**Delivered:** an --category filter on the tally command\n", encoding="utf-8")
    out = squash_message({"title": "Tally by category"}, "abc123abc123", idir, good)
    ok("the declared type + subject win over the item title",
       out.splitlines()[0] == "fix: Reject empty category names")
    ok("the body is what shipped", "--category filter" in out)
    ok("the item id rides in the trailers", out.strip().endswith("SuperMe-Item: abc123abc123"))
    fallback = squash_message({"title": "Tally by category"}, "abc123abc123", idir, None)
    ok("no declaration → the title under the default type, honestly a fallback",
       fallback.splitlines()[0] == "feat: Tally by category")

    child = squash_message({"title": "Child", "spawned_from": {"item": "parent99", "relation":
                                                               "blocking"}},
                           "child77", idir, {"type": "feat", "subject": "Add the parser hook"})
    ok("a branch-off child declares its commit the same way",
       child.splitlines()[0] == "feat: Add the parser hook")
    ok("...and its parentage is a trailer, not prose", "SuperMe-Parent: parent99" in child)

    class _Store:
        def list_events(self, *a, **k):
            return [{"kind": "run.report", "meta": {"machine": {"outcome": "revise"}}},
                    {"kind": "run.report", "meta": {"machine": {"outcome": "success",
                                                                "commit": good}}}]
    ok("the declaration is read back from the run trail, newest valid one first",
       declared_commit(_Store(), "ctx", "itm") == good)

    # --- the branch commits follow the same split -------------------------------------
    style = Path("superme_agent/harness/plugins/superme-dev/skills/build/references/"
                 "commit-style.md").read_text(encoding="utf-8")
    ok("per-task commits carry the task id as a TRAILER", "SuperMe-Task: t<n>" in style)
    ok("...not as a subject prefix", "`t<n>: <what changed>`" not in style)
    ok("a check-fix names its check below the line too", "SuperMe-Check: c4" in style)
    ok("the split is stated as the rule", "Trailer block" in style)
    gl = src("superme_agent/core/git_layer.py")
    ok("the child merge no longer names branches in its subject",
       'f"Merge {child_branch} into {state[\'branch\']}"' not in gl)


def test_pr_gate_and_page(tmp: Path) -> None:
    """The second gate, and the surface it exists for.

    `strict` splits approval: the deputy's opens the PR, the owner's merges. The state is DERIVED,
    so the merge closes the PR."""
    import subprocess
    from superme_agent.core import git_layer as G, artifacts as A
    from superme_agent.daemon.services import git_ops, pr_view

    print("slice 4d — pr_open, and the task-grouped walkthrough")

    # --- the state ---------------------------------------------------------------------
    ok("no stamp → no PR", git_ops.pr_open({}) is False)
    ok("stamped and unmerged → the PR is open", git_ops.pr_open({"git_pr_opened_at": "t"}) is True)
    ok("...and the MERGE is what closes it — no flag to clear",
       git_ops.pr_open({"git_pr_opened_at": "t", "git_merge_commit": "abc"}) is False)
    from superme_agent.core.dev_knowledge import DevKnowledgeService
    ok("the stamp is a known git-record field (an unknown key raises, loudly)",
       "git_pr_opened_at" in DevKnowledgeService._GIT_FIELDS)

    gates_src = src("superme_agent/daemon/services/gates.py")
    ok("the strict gate keys off the ACTOR — that IS what the mode governs",
       'if actor != "owner" and not autopilot_core.is_prompt_extraction(item) \\\n'
       '                and git_ops.repo_review_mode(ctx, spine) == "strict":' in gates_src)
    ok("...and it sits BEFORE the merge, so a strict repo can never merge without the owner",
       gates_src.index("git_ops.open_pr(") < gates_src.index("review_merge_out = git_ops.review_merge("))
    ok("the throwaway probe is exempt (it must sail through every gate to be captured)",
       "is_prompt_extraction(item)" in gates_src.split("review_mode(ctx, spine)")[0][-400:])
    # The INVARIANT, not the wording: pinning owner-editable copy makes an edit look like a
    # regression.
    import superme_agent.daemon.services.attention as ATT
    base = {"phase": "review", "status": "awaiting_human"}
    plain = ATT.classify_hold(base, [])
    pr_open = ATT.classify_hold({**base, "git_pr_opened_at": "2026-07-31T00:00:00"}, [])
    ok("the attention card names the narrower act when the PR is open",
       plain["kind"] == pr_open["kind"] == "review" and plain["reason"] != pr_open["reason"]
       and bool(pr_open["reason"].strip()))
    ok("...and a MERGED item is back to the plain review reason",
       ATT.classify_hold({**base, "git_pr_opened_at": "x", "git_merge_commit": "abc"},
                         [])["reason"] == plain["reason"])

    # `strict` governs who ELSE may land a branch, never what the OWNER's approval does.
    modal = src("web/frontend/src/features/dev/WorkItemModal.tsx")
    # The label comes from the SERVER, so the invariant is asserted where it is decided.
    ok("the owner's gate button always says merge — never conditioned on review_mode",
       "a.label" in modal and "'Approve & open PR'" not in modal
       and "Approve & merge" not in modal)
    ok("the Git tab's landing line NAMES THE ACTOR (it is the deputy's approval that opens a PR)",
       "the deputy's approval only opens a PR; yours merges" in modal
       and "strict — approving opens a PR; you merge from the PR page" not in modal)
    # Comments STRIPPED: this file quotes the wrong label, and a raw search would read that as the
    # bug.
    dd_src = src("superme_agent/daemon/services/drilldown.py")
    dd = "\n".join(l for l in dd_src.splitlines() if not l.lstrip().startswith("#"))
    ok("...and the server's review label says merge, unconditionally",
       'approve_label, approve_does = "Approve & merge", (' in dd
       and "Approve & open PR" not in dd)
    ok("the mode may only change WORDING THAT NAMES THE DEPUTY",
       'if mode == "strict" else ""' in dd
       and "the DEPUTY cannot land it" in dd)

    # --- a spent approval closes the PR ----------------------------------------
    # Revise and re-vet leave review unmerged, so a surviving stamp strands it.
    dev = DevKnowledgeService()
    close_root = make_dev_root(tmp, "root-closepr")
    dev.set_work_item_git(close_root, "it7", git_pr_opened_at="2026-07-29T10:00:00")
    ok("(fixture) the item starts with an open PR",
       git_ops.pr_open(dev.read_work_item(close_root, "it7")) is True)
    git_ops.close_pr(dev, close_root, "it7")
    reread = dev.read_work_item(close_root, "it7")
    ok("close_pr clears the stamp — the approval is spent, so the PR closes",
       not reread.get("git_pr_opened_at") and git_ops.pr_open(reread) is False)
    git_ops.close_pr(dev, close_root, "it7")
    ok("...and it is idempotent (an item that never had a PR is untouched, not broken)",
       git_ops.pr_open(dev.read_work_item(close_root, "it7")) is False)

    runs_src = src("superme_agent/daemon/services/runs.py")
    revise_block = runs_src.split('if phase == "review":', 1)[1][:600]
    ok("a `revise` closes the PR as it routes the item back to plan",
       "close_pr(_dev, dev_root, item_id)" in revise_block
       and revise_block.index("close_pr") < revise_block.index("set_work_item_phase"))
    revet_block = gates_src.split('freshness") == "revet"', 1)[1][:900]
    ok("...and so does the freshness send-back, inside the phase CAS so it can't fire twice",
       "git_ops.close_pr(dev, dev_root, item_id)" in revet_block
       and revet_block.index("close_pr") > revet_block.index('_cas_phase(dev_root, item_id, "review", "vet")'))
    ok("the merge path does NOT clear it — the merge commit is what closes that PR",
       "close_pr" not in gates_src.split('freshness") == "park"', 1)[1])

    # --- the commit contract the walkthrough depends on -------------------------
    # The requirement sits IN the step that commits.
    build_skill = " ".join(src("superme_agent/harness/plugins/superme-dev/skills/build/SKILL.md").split())
    step2 = build_skill.split("## Step 2", 1)[1].split("\n## ", 1)[0]
    ok("build's commit step names the trailer itself, not just a reference to read",
       "SuperMe-Task: t<n>" in step2 and "references/commit-style.md" in step2)
    ok("...and says where a task id may NOT go, which is what the live builds got wrong",
       "no task ids, item ids or phase names in the subject" in step2.lower())

    # --- the surfaces the conflict and review paths needed ------------------------- The refusal
    # names a control, so that control must exist.
    modal = src("web/frontend/src/features/dev/WorkItemModal.tsx")
    api_imports = "".join(re.findall(r"import[\s\S]*?from '@/lib/api'", modal))
    ok("the park refusal's own escape hatch is reachable — a component calls resolveWorkItemGit",
       "resolveWorkItemGit(it.id, contextId)" in modal
       and "resolveWorkItemGit," in api_imports)
    # Always rendered, disabled with the reason: an absent button reads as a missing feature. The
    # gate itself is unchanged.
    ok("...always rendered, and live only when the branch is behind (the one conflict-possible state)",
       "disabled={!!health.merged || !health.behind}" in modal
       and "Offered when the branch is behind" in modal)
    routes_src = src("superme_agent/daemon/routers/dev/git.py")
    ok("...and the route it calls still refuses a clean sync rather than firing a pointless run",
       "nothing to resolve" in routes_src)

    # The PR page is its OWN browser tab: a diff read in a leftover third is a diff nobody reads.
    prpage = src("web/frontend/src/features/dev/PrPage.tsx")
    entry = src("web/frontend/src/main.tsx")
    dash = src("web/frontend/src/features/dev/DevDashboard.tsx")
    # Being addressable and being its own document are independent; this is about the second.
    ok("the PR path forks at the root: the PR page IS the document, App never mounts",
       "route.name === 'pr'" in entry and "<PrPage" in entry and "<App />" in entry)
    ok("...and a tab parked on the old `?repo=&pr=` form is still honoured, rewritten in place",
       "q.get('pr')" in entry and "replaceState" in entry)
    ok("...so the PR tab carries none of the cockpit's polling", "useCommandStats" not in prpage)
    ok("...and the page takes the whole viewport", "fixed inset-0" in prpage)
    # A real anchor, not a scripted popup: a browser may decline a popup, and navigation always
    # happens.
    ok("the Git tab's button is a link to that URL, opened in a new tab",
       'target="_blank"' in modal and "build({ name: 'pr'" in modal)
    ok("...and nothing renders it as an in-app view any more",
       "PrPage" not in modal and "openPr" not in dash)

    # Flipping the phase without dispatching leaves the item active with nothing working.
    runs_src = src("superme_agent/daemon/services/runs.py")
    resolve_src = runs_src.split("async def run_background_resolve", 1)[1].split("\nasync def ", 1)[0]
    ok("a resolved conflict actually STARTS the vet it re-enters",
       "start_vet_run(ctx, context_id, item_id)" in resolve_src)
    ok("...after `end_run`, since the resolve run holds the item's lock until then",
       resolve_src.index("start_vet_run(ctx") > resolve_src.index('final_usage, outcome=outcome'))
    ok("...and if it can't start, the item rests where the owner SEES it, never `active` with no run",
       'set_work_item_status(dev_root, item_id, "awaiting_human")' in resolve_src)

    # --- the branch the page reads -----------------------------------------------------
    repo = tmp / "pr-repo"
    repo.mkdir()

    def g(*a, cwd=repo):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8")

    g("init", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "tally.py").write_text("\n".join(f"line {i}" for i in range(20)) + "\n", encoding="utf-8")
    g("add", "."); g("commit", "-m", "init")
    g("checkout", "-b", "wi/pr")

    def commit(subject, trailers, files):
        for path, body in files.items():
            (repo / path).write_text(body, encoding="utf-8")
        g("add", ".", cwd=repo)
        g("commit", "-m", G.compose_commit(subject, "", trailers), cwd=repo)

    commit("Add a --category flag to tally", {"SuperMe-Task": "t1"},
           {"tally.py": "\n".join(f"line {i}" for i in range(20)) + "\nflag\n"})
    commit("Checkpoint the parser split", {"SuperMe-Task": "t1 (wip)"},
           {"parser.py": "split\nmore\nlines\n"})
    commit("Reject empty category names", {"SuperMe-Task": "t2", "SuperMe-Check": "c4"},
           {"guard.py": "guard\n"})
    commit("Tidy the imports", {}, {"parser.py": "split\nmore\nlines\nimport os\n"})
    # An anchor commit merged in mid-build — the sync the walkthrough must NOT attribute here.
    g("checkout", "main"); (repo / "elsewhere.py").write_text("someone else\n", encoding="utf-8")
    g("add", "."); g("commit", "-m", "Someone else's work")
    g("checkout", "wi/pr"); g("merge", "main", "-m", "sync")

    commits = G.branch_commits(repo, "wi/pr", "main")
    subjects = [c["subject"] for c in commits]
    ok("every commit this branch added is present, oldest first",
       subjects == ["Add a --category flag to tally", "Checkpoint the parser split",
                    "Reject empty category names", "Tidy the imports"])
    ok("the freshness sync is dropped — its work is the anchor's, not this item's",
       "sync" not in subjects and all("elsewhere.py" not in [f["path"] for f in c["files"]]
                                      for c in commits))
    ok("trailers are parsed off the body", commits[2]["trailers"]
       == {"SuperMe-Task": "t2", "SuperMe-Check": "c4"})
    ok("per-file churn rides each commit",
       commits[0]["files"] == [{"path": "tally.py", "plus": 1, "minus": 0}])
    ok("a mid-body `Key: value` line is NOT a trailer (git's own last-block rule)",
       G.commit_trailers("body\nNote: see X\n\nmore prose") == {})

    plan = ("# Plan\n\n## Tasks\n- [x] t1 — Add the flag and split the parser\n"
            "- [x] t2 — Reject empty categories\n- [ ] t3 — Never built\n")
    tasks = A.parse_tasks(plan)
    ok("plan tasks parse into the join key", [t["id"] for t in tasks] == ["t1", "t2", "t3"])
    groups = pr_view._group_commits(commits, tasks)
    ok("groups come out in PLAN order, and an unbuilt task shows no group",
       [gp["task"] for gp in groups] == ["t1", "t2", None])
    ok("a `(wip)` checkpoint belongs to ITS task, not a group of its own",
       len(groups[0]["commits"]) == 2)
    ok("...and the group is titled with the plan's own words",
       groups[0]["title"] == "Add the flag and split the parser")
    ok("a commit with no task trailer is shown last, never hidden",
       groups[-1]["commits"][0]["subject"] == "Tidy the imports")
    ok("files inside a group are churn-ranked (the biggest change is where the risk is)",
       [f["path"] for f in groups[0]["files"]] == ["parser.py", "tally.py"])
    ok("the trailer block is stripped from the body the page shows",
       all("SuperMe-" not in (c["body"] or "") for gp in groups for c in gp["commits"]))

    stat = G.branch_stat(repo, "wi/pr", "main")
    ok("the header stat is the NET diff, not the sum of per-commit churn",
       stat["files"] == 3 and stat["insertions"] == 6)
    patches = G.commit_patches(repo, [c["sha"] for c in commits
                                      if pr_view.task_of(c) == "t1"], "parser.py")
    ok("a file's patches are per-commit — exact even when a task's commits aren't contiguous",
       len(patches) == 1 and "+split" in patches[0]["patch"]
       and "import os" not in patches[0]["patch"])   # the untagged commit's change is NOT here
    ok("truncation is reported, never silent",
       G.commit_patches(repo, [commits[0]["sha"]], "tally.py", cap=10)[0]["truncated"] is True)


def test_commit_gate(tmp: Path) -> None:
    """The commit contract, enforced by a hook rather than asked for in prose.

    A refusal build cannot author parks the item."""
    import subprocess
    from superme_agent.core import git_layer as GL
    from superme_agent.core import permissions as P
    from superme_agent.daemon.services import loop as L

    repo = tmp / "hook-repo"
    repo.mkdir()

    def g(*a, cwd=repo):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8")

    g("init", "-b", "main"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
    (repo / "a.txt").write_text("x", encoding="utf-8"); g("add", "-A"); g("commit", "-m", "seed")
    ok("the commit gate installs into the repo's shared hooks dir",
       GL.install_commit_hook(repo)["installed"] is True
       and (repo / ".git" / "hooks" / "commit-msg").exists())

    # The owner's own branch is not the agent's, and this hook has no business there.
    (repo / "a.txt").write_text("y", encoding="utf-8"); g("add", "-A")
    ok("...and leaves every branch that isn't an item branch alone",
       g("commit", "-m", "a plain message with no trailer").returncode == 0)

    g("checkout", "-b", "item/abc123-probe")
    (repo / "a.txt").write_text("z", encoding="utf-8"); g("add", "-A")
    refused = g("commit", "-m", "Add a thing")
    ok("a commit on an item branch with no task trailer is REFUSED", refused.returncode == 1)
    ok("...and the refusal says the exact line to add, not just that it is wrong",
       "SuperMe-Task: t3" in refused.stderr)
    ok("...and tells the agent what to do when the refusal is NOT ours: park, don't retry",
       "needs_user" in refused.stderr and "--no-verify" in refused.stderr)
    ok("...while the same commit WITH the trailer lands",
       g("commit", "-m", "Add a thing\n\nSuperMe-Task: t2").returncode == 0)
    ok("...as does a wip checkpoint between tasks (commit-style.md's own form)",
       g("commit", "--allow-empty", "-m", "Checkpoint\n\nSuperMe-Task: t2 (wip)").returncode == 0)

    # The kernel's own commits are merges, and belong to no task.
    g("checkout", "main"); (repo / "b.txt").write_text("m", encoding="utf-8"); g("add", "-A"); g("commit", "-m", "trunk")
    g("checkout", "item/abc123-probe")
    ok("...and a MERGE on the item branch still passes (it is the kernel's, and task-less)",
       g("merge", "main", "-m", "Sync main into item/abc123-probe").returncode == 0)

    # Refusing to install is the interesting half: both cases are a real project's real setup.
    foreign = tmp / "foreign-repo"
    foreign.mkdir()
    g("init", "-b", "main", cwd=foreign)
    hooks = foreign / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "commit-msg").write_text("#!/bin/sh\n# somebody else's gate\nexit 0\n", encoding="utf-8")
    out = GL.install_commit_hook(foreign)
    ok("a commit-msg hook the project already owns is NOT overwritten",
       out["installed"] is False and out["reason"] == "foreign"
       and "somebody else's gate" in (hooks / "commit-msg").read_text(encoding="utf-8"))

    routed = tmp / "husky-repo"
    routed.mkdir()
    g("init", "-b", "main", cwd=routed)
    g("config", "core.hooksPath", ".myhooks", cwd=routed)
    out = GL.install_commit_hook(routed)
    ok("...and a repo that routes hooks elsewhere refuses rather than installing a dead file",
       out["installed"] is False and out["reason"] == "hooks_path_override")

    gates_src = src("superme_agent/daemon/services/gates.py")
    ok("...with the owner told, since silent absence would read as enforcement",
       '"git.hook"' in gates_src and "NOT enforced" in gates_src)

    # Bypass. An agent whose commit is refused finds the escape flag first, so it is taken away.
    ok("`git commit --no-verify` is a bypass", P.bypasses_commit_hooks("git commit --no-verify -m x"))
    ok("...and so is its short form", P.bypasses_commit_hooks("git commit -n -m x"))
    ok("...and a merge that skips hooks", P.bypasses_commit_hooks("git merge main --no-verify"))
    ok("an ordinary commit is not", not P.bypasses_commit_hooks("git commit -m 'x'"))
    ok("...and neither is a `-n` that belongs to something else on the line",
       not P.bypasses_commit_hooks("grep -n foo file.py && git commit -m 'x'"))
    perms_src = src("superme_agent/core/permissions.py")
    bash_branch = perms_src.split('if tool_name == "Bash":', 1)[1]
    ok("...and the check runs BEFORE the read-only fast path, so nothing slips past it",
       bash_branch.index("bypasses_commit_hooks") < bash_branch.index("is_read_only_bash"))

    # `git -C <worktree>` is the idiom close is told to use, so positional reading refuses it.
    ok("a git read scoped with a global option is still a read",
       P.is_read_only_bash("git -C /tmp/wt diff --stat main...HEAD")
       and P.is_read_only_bash("git --git-dir=/tmp/wt/.git log --oneline")
       and P.is_read_only_bash("git -c core.pager=cat show HEAD"))
    ok("...and skipping the option's VALUE keeps a path from passing as a subcommand",
       not P.is_read_only_bash("git -C /tmp/wt"))
    ok("...while the same scoping never launders a mutation",
       not P.is_read_only_bash("git -C /tmp/wt commit -m x")
       and not P.is_read_only_bash("git -C /tmp/wt push")
       and not P.is_read_only_bash("git -C /tmp/wt branch -D old"))

    # The routing rule: unsolvable → ask, never loop.
    item = {"status": "active", "phase": "build"}
    ok("a build that hits a wall only the owner can clear STOPS and asks",
       L.decide_after_build(item, outcome="needs_user", turn_error=False)
       == {"stopping": True, "klass": "needs_user"})
    ok("...while an ordinary cycle still advances to vet (BV-A1 unchanged)",
       L.decide_after_build(item, outcome="success", turn_error=False)["stopping"] is False)
    ok("...and so does a recorded assumption or a deferred authorization",
       L.decide_after_build(item, outcome="partial", turn_error=False)["stopping"] is False
       and L.decide_after_build(item, outcome="approval_required",
                                turn_error=False)["stopping"] is False)
    loop_src = src("superme_agent/daemon/services/loop.py")
    ok("...and the ask rests the item where the owner sees it",
       'needs_user — the question is the run' in loop_src and "asking" in loop_src)
    # The commit wall is the ONE state resting inside the loop: nothing landed, so review would
    # show an empty diff.
    ok("a stopped build turn holds at build as `error`, never advanced to review",
       'mark_item_error(ctx, context_id, item_id, reason, phase="build")' in loop_src
       and '"exit": "error"' in loop_src
       and '_cas_phase(dev_root, item_id, "build", "review")' not in loop_src)

    skill = src("superme_agent/harness/plugins/superme-dev/skills/build/SKILL.md")
    ok("build's own contract says a refused commit is read, not retried",
       "never `--no-verify`" in skill and "needs_user" in skill and "the refusal verbatim" in skill)


def test_build_loop_entry() -> None:
    """Approving the plan gate is the instruction to build, so entering `build` opens the loop.

    For every item: a hand-driven one otherwise has nothing to start it."""
    print("B6 — the build⟷vet loop opens for every item, autopilot or not")
    from superme_agent.daemon.services import gates as G
    gates_src = src("superme_agent/daemon/services/gates.py")

    ok("entering build is NOT autopilot-gated",
       'if nxt == "build":' in gates_src and 'if nxt == "build" and autopiloted:' not in gates_src)
    ok("...and it opens the loop through the shared entry",
       "create_task(enter_build_loop(context_id, item_id))" in gates_src)
    ok("the entry fires the loop's OPENING cycle (build-first), not a vet against an empty tree",
       "loop_svc.start_first_build(ctx, context_id, item_id)" in gates_src)
    _ebl = gates_src.split("async def enter_build_loop")[1]
    _ebl = _ebl.split("\nasync def ")[0].split("\ndef ")[0]
    ok("enter_build_loop is not itself autopilot-gated", "is_autopilot" not in _ebl)
    # What autopilot still gates: the deputy judging on the owner's behalf. That IS the
    # delegation.
    ok("the review branch stays autopilot-gated (deputy judgment is the delegated act)",
       'elif nxt == "review" and autopiloted and not auto_started:' in gates_src)
    ok("...and close's runner was already actor-independent, same reasoning",
       'elif nxt == "close":' in gates_src)


# ------------------------------------------------- the drilldown payload + Proof

def test_proof_rows(tmp: Path) -> None:
    """The connected view. The join key is the plan task id, carried by both records."""
    print("Proof rows — one row per built thing, joined mechanically on task ids")
    from superme_agent.core import artifacts as A
    d = tmp / "proof-item"
    (d / "artifacts").mkdir(parents=True)
    (d / "artifacts" / "plan.md").write_text(
        "# Plan — p\n\n## Tasks\n- [x] t1 — CSV export on the stats page\n"
        "- [ ] t2 — keep the `sum` alias routing\n\n## Verification plan\n"
        "depth: checks\nreason: user-visible output\nenv: none\n\n"
        "### csv-downloads\n- traces: d-reporting\n- covers: t1\n- mode: command\n"
        "- scenario: curl\n- expect: 200 + text/csv\n\n"
        "### suite-green\n- traces: no regressions\n- mode: command\n- scenario: pytest\n"
        "- expect: all pass\n\n"
        # Planned but never run — the case the Task tab exists to show at the plan gate.
        "### alias-routes\n- traces: d-reporting\n- covers: t2\n- mode: command\n"
        "- scenario: curl /sum\n- expect: 302 to /stats\n", encoding="utf-8")
    # The id is BOLDED because real reports write it that way; a tolerant parser hid the bug.
    (d / "artifacts" / "build-vet-1.md").write_text(
        "# Build⟷vet 1 — p\n\n## Built\n- **t1** (`web/stats.py`): added the CSV writer\n"
        "  and wired the download button\n- refreshed a stale doc stub\n\n"
        "## Validation\n- t1 — 12 unit tests pass\n- suite green (42)\n\n"
        "## Verification\n```\n"
        "### 2026-07-30T10:00:00 — csv-downloads\n- how: curl -s /stats.csv\n"
        "- result: 200 text/csv\n- passed: false\n- fingerprint: a\n"
        "### 2026-07-30T10:01:00 — suite-green\n- how: pytest\n- result: 42 passed\n"
        "- passed: true\n- fingerprint: a\n```\n\n## Cycle outcome\n- exit: build\n", encoding="utf-8")
    (d / "artifacts" / "build-vet-2.md").write_text(
        "# Build⟷vet 2 — p\n\n## Built\n- t1 — fixed the content type\n\n"
        "## Validation\n- t1 — 13 unit tests pass\n\n## Verification\n```\n"
        "### 2026-07-30T11:00:00 — csv-downloads\n- how: curl -s /stats.csv\n"
        "- result: 200 text/csv\n- passed: true\n- fingerprint: b\n```\n\n"
        "## Cycle outcome\n- exit: converged\n", encoding="utf-8")

    rows = A.proof_rows(d)
    by = {r["task"]: r for r in rows}
    ok("every plan task gets a row, in plan order",
       [r["task"] for r in rows] == ["t1", "t2", ""])
    ok("the row's LABEL is the task text — a feature in plain words, not a check id",
       by["t1"]["text"] == "CSV export on the stats page")
    ok("an EMPHASIZED task id still joins, and the `**` never leaks into the text",
       by["t1"]["built"] and by["t1"]["built"][0].startswith("(`web/stats.py`)")
       and "**" not in by["t1"]["built"][0])
    ok("a wrapped `## Built` bullet is ONE entry, continuation folded in",
       len(by["t1"]["built"]) == 2
       and "wired the download button" in by["t1"]["built"][0])
    ok("built entries accumulate across cycles", "fixed the content type" in by["t1"]["built"][1])
    ok("validation joins on the same id", by["t1"]["validated"] == ["12 unit tests pass",
                                                                   "13 unit tests pass"])
    ok("a check joins via `covers:`", [v["check"] for v in by["t1"]["verified"]] == ["csv-downloads"])
    ok("...carrying its LATEST verdict, not every entry",
       by["t1"]["verified"][0]["passed"] and by["t1"]["verified"][0]["cycle"] == 2)
    # Latest-only would hide the loop's whole story — `c3 ✗→✓` is the thing worth showing.
    ok("...plus the per-cycle history, so ✗→✓ is renderable",
       [h["passed"] for h in by["t1"]["verified"][0]["history"]] == [False, True])
    ok("a task nothing touched renders empty rather than vanishing", by["t2"]["built"] == [])
    # The exam is decided at PLAN, so a check the loop has not reached is a row, marked not-yet-
    # run.
    ok("a planned check is a row before anything runs",
       [v["check"] for v in by["t2"]["verified"]] == ["alias-routes"]
       and by["t2"]["verified"][0]["ran"] is False)
    ok("...and it carries what it will prove, from the plan",
       by["t2"]["verified"][0]["expect"] == "302 to /stats"
       and by["t2"]["verified"][0]["mode"] == "command")
    ok("a check WITH a verdict says so", by["t1"]["verified"][0]["ran"] is True)
    ok("UNTAGGED built/validation land item-wide, never under whichever task was open",
       by[""]["built"] == ["refreshed a stale doc stub"]
       and by[""]["validated"] == ["suite green (42)"])
    ok("...and so does a check that names no task",
       [v["check"] for v in by[""]["verified"]] == ["suite-green"])

    # The tags are TAUGHT, never required: a hard issue would retroactively fail every in-flight
    # plan.
    vp = A.parse_vet_plan((d / "artifacts" / "plan.md").read_text(encoding="utf-8"))
    ok("a check with no `covers:` is still structurally valid",
       not any("covers" in i for i in A.vet_plan_hard_issues(vp)))
    plan_tmpl = Path("superme_agent/harness/plugins/superme-dev/skills/plan/templates/"
                     "plan-template.md").read_text(encoding="utf-8")
    ok("the plan template teaches `covers:`", "- covers:" in plan_tmpl)
    cyc_tmpl = Path("superme_agent/harness/plugins/superme-dev/skills/build/templates/"
                    "build-vet-template.md").read_text(encoding="utf-8")
    ok("the cycle template teaches the leading task id in BOTH sections",
       cyc_tmpl.count("LEADING with its task id") + cyc_tmpl.count("LEAD with the task id") >= 2)


def test_drilldown_payload(tmp: Path) -> None:
    """The ONE server-computed payload. What is asserted is the part the FE must never re-derive:
    every control's `active` + `reason`, and the card that answers "what is needed from me"."""
    print("drilldown payload — server-computed activation + the WHAT-YOU-NEED-TO-DO card")
    from superme_agent.daemon.services import drilldown as DD
    dev_root = make_dev_root(tmp, "root-dd", phase="review")
    item_dir = dev_root / "work-items" / "it7"
    (item_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    (item_dir / "reports").mkdir(parents=True, exist_ok=True)
    # A CLEAN review gate needs COMPLETE artifacts: readiness is judged where there is recourse.
    from superme_agent.core import artifacts as _A

    def _artifact(kind: str, **sections: str) -> None:
        _A.scaffold(item_dir, kind, title="S6", item_kind="implementation")
        f = item_dir / "artifacts" / _A.artifact_file(kind)
        text = re.sub(r"<fill:[^>]*>", "filled", f.read_text(encoding="utf-8"))
        for sec, body in sections.items():
            text = re.sub(rf"(?ms)(^##\s+{re.escape(sec)}\s*\n).*?(?=^##\s|\Z)",
                          rf"\g<1>{body}\n\n", text)
        f.write_text(text, encoding="utf-8")

    _artifact("plan", Tasks="- [x] t1 — a thing",
              **{"Verification plan": "depth: none\nreason: nothing observable\nenv: none"})
    _artifact("review")
    (item_dir / "reports" / "report-review.md").write_text("# Review\nlanded.\n", encoding="utf-8")
    item = {"id": "it7", "title": "S6", "kind": "implementation", "phase": "review",
            "status": "awaiting_human", "git_branch": "wi/it7"}

    def payload(**over):
        return DD.build_payload({**item, **over}, item_dir, dev_root, None,
                                all_items=[item], events=over.pop("_events", []),
                                git_health=over.pop("_git", None),
                                review_mode=over.pop("_mode", None))

    d = payload()
    acts = {a["id"]: a for a in d["actions"]}
    # THREE bar controls plus the two git ones: primary, Drop, Re-run.
    ok("the bar's closed set is primary(approve|run|resume) + drop + rerun, plus the git one",
       set(acts) == {"approve", "run", "resume", "drop", "rerun", "pr"})
    # Exactly ONE control lands the work. A second one, gated differently, is also the bypass.
    ok("no second control performs the gate's act", "merge" not in acts)
    ok("...and every bar control is homed in the action bar",
       {a for a, v in acts.items() if v["home"] == "actions"}
       == {"approve", "run", "resume", "drop", "rerun"})
    # Resume is ALWAYS rendered, greyed with its reason: a control that explains itself teaches,
    # an absent one hides.
    ok("Resume is rendered even on an item that never stopped", "resume" in acts)
    ok("...inactive, saying when it WOULD appear",
       not acts["resume"]["active"] and "nothing has stopped" in acts["resume"]["reason"])
    ok("EVERY control carries a reason, live or not — not just the disabled ones",
       all(a["reason"] for a in d["actions"]))
    ok("git controls are homed on the Git tab, not the frame's action bar",
       acts["pr"]["home"] == "git" and acts["approve"]["home"] == "actions")
    ok("Drop is always live while the item lives ", acts["drop"]["active"])
    ok("the owner's review label says MERGE — the act is the same in both modes",
       acts["approve"]["label"] == "Approve & merge")
    # `review_mode` is a REPO fact and arrives on its own; an absent mode must claim NEITHER.
    def approve_reason(**over):
        return next(a for a in payload(**over)["actions"] if a["id"] == "approve")["reason"]

    ok("a strict repo says strict, even with no git health to read",
       "`strict`" in approve_reason(_mode="strict"))
    ok("...and an unknown mode claims NEITHER mode",
       "`strict`" not in approve_reason() and "`fast`" not in approve_reason())
    ok("depth `none` means nothing is owed, so Approve is live",
       acts["approve"]["active"] and d["blocked_by"] == [])
    ok("...and the reports list names only what exists", d["reports"] == ["review"])
    # Nothing open: the card names the ONE control that performs the act.
    ok("a clean review gate's card points at Approve itself",
       d["attention"] and d["attention"]["click"] == "approve")

    # A pending authorization greys Approve, and the REASON is the check's own detail.
    from superme_agent.core import artifacts as A
    A.record_authorization(item_dir, what="drop a stale doc", why="it lies",
                            doc="architecture.md", scope="roadmap-scope", check="c1")
    d = payload()
    acts = {a["id"]: a for a in d["actions"]}
    ok("a pending authorization greys Approve", not acts["approve"]["active"])
    ok("...and the button's reason NAMES it", "authorization" in acts["approve"]["reason"])
    ok("...and it rides the typed feed the grant/deny UI renders",
       len(d["authorizations"]) == 1 and d["authorizations"][0]["what"] == "drop a stale doc")

    # A running agent greys the gate: the decision is not the owner's while work is in flight.
    d = payload(running=True)
    ok("a run in flight greys Approve and says why",
       not next(a for a in d["actions"] if a["id"] == "approve")["active"])

    # The card: hidden when nothing needs the owner, and naming BOTH the act and the one control.
    ok("an active item needs nothing → no card at all", payload(status="active")["attention"] is None)
    card = payload()["attention"]
    ok("a parked item gets a card with why · do · basis",
       card and card["why"] and card["do"] and card["basis"])
    # With the must-resolve set open there is no ONE control to press, so the card says so.
    ok("...and with something must-resolve open it says resolve, naming no button",
       "Resolve what" in card["do"] and card["click"] == "")
    # REFERENCE points at a SURFACE, and carries the COUNT standing between owner and button.
    ok("...and REFERENCE points at the open checks by count",
       any(str(len(payload()["blocked_by"])) in b for b in card["basis"]))
    ok("...and it points at the phase's report too",
       any("Reports" in b and phase_label in b
           for b in card["basis"] for phase_label in [str(payload()["phase"]).title()]))
    grill = payload(_events=[{"kind": "run.report", "actor": "agent", "summary": "3 questions",
                             "meta": {"outcome": "needs_user", "user": {"questions": [
                                 {"question": "TTL — 24h or 7d?", "recommend": "24h"}]}}}])
    ok("a grill hold carries the questions and points at the chat",
       grill["attention"]["kind"] == "question"
       and grill["attention"]["click"] == "chat"
       and grill["attention"]["questions"][0]["question"].startswith("TTL"))

    # What the item IS, not a restatement of the header. A row with nothing in it never renders.
    about = payload()["about"]
    ok("About is an ordered list of rows, not a map",
       isinstance(about, list) and all(set(r) == {"label", "value"} for r in about))
    ok("...and an empty row is dropped rather than rendered blank",
       all(r["value"] for r in about))
    ok("...and it leads with what KIND of work this is",
       about and about[0]["label"] == "Workflow" and about[0]["value"] == "implementation")
    ok("the glance it replaced is gone from the payload", "glance" not in payload())

    # The card renders the phase's summary line alone, and says nothing when there is none.
    ok("a phase with no report yet reports no summary", payload()["now"]["summary"] == "")
    (item_dir / "reports" / "report-review.md").write_text(
        "# Review User-facing Report\n\n**Summary:** it holds, and the flag is consistent now.\n", encoding="utf-8")
    ok("...and once the report exists the card gets its one line",
       payload()["now"]["summary"] == "it holds, and the flag is consistent now.")
    # The FE must not re-decide this. Comments STRIPPED: the header names the field it replaced.
    modal_src = src("web/frontend/src/features/dev/WorkItemModal.tsx")
    modal_code = "\n".join(l for l in modal_src.splitlines()
                           if not l.lstrip().startswith(("//", "*", "/*")))
    ok("the component reads `active`/`reason` and computes no activation of its own",
       "disabled={!a.active || busy}" in modal_code
       and "approve_blocked_by" not in modal_code)
    ok("...and the gate brief's pane is GONE with it",
       "GateBriefPane" not in modal_code and "report_html" not in modal_code
       and "srcDoc" not in modal_code)
    # Each proof leads with what the check PROVES. Older plans predate the field, so the fallback
    # chain has to survive.
    ok("a proof row leads with the check's own sentence",
       "v.proves || v.expect || v.check" in modal_code)
    ok("...with the machine detail one click away, not gone",
       "how this was checked" in modal_code and "<details" in modal_code)
    # The pane speaks only when something is stopping the owner, on the same condition that greys
    # the button.
    ok("the checks block renders only when something must resolve",
       "d.blocked_by.length > 0 && d.at_gate && !d.now.running" in modal_code)
    ok("...and the pane no longer previews a gate nobody is at",
       "preview of" not in modal_code and "For your information" not in modal_code)
    ok("`Item at a glance` is gone from the component too", "d.glance" not in modal_code)


def main() -> None:
    with TemporaryDirectory() as td:
        tmp = Path(td)
        test_commit_gate(tmp)
        test_repo_knobs(tmp)
        test_the_checkers_run_on_their_own_tier()
        test_prompt_xray_covers_every_speaker()
        test_repo_activity_scope(tmp)
        test_branch_list(tmp)
        test_merge_act(tmp)
        test_commit_contract(tmp)
        test_pr_gate_and_page(tmp)
    with TemporaryDirectory() as td:
        tmp = Path(td)
        test_plan_revision(tmp)
        test_pre_grammar_plan_migrates(tmp)
        test_generation_scoping(tmp)
        test_tool(tmp)
        test_revise_outcome_routes(tmp)
        test_fire_phase_feedback_owner(tmp)
        test_gate_check(tmp)
        test_plan_readonly_at_review(tmp)
        test_proof_rows(tmp)
        test_drilldown_payload(tmp)
    test_hold_and_compaction_hooks()
    test_decision_bubbles()
    test_routing_rule_is_per_turn()
    test_completion_card()
    test_chat_can_report()
    test_contracts()
    test_review_entry_run()
    test_build_loop_entry()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
