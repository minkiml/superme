"""The phase-session contract and its continuity, without a daemon or an LLM.

The completion tool validates its payload and delivers it through the sink; worker scoping
refuses cross-item and unbound calls on the REAL factories.

Run: PYTHONPATH=. python -m scripts.test_ws_s5
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from superme_agent.core import artifacts as A
from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.core.kernel_speech import work_item_preamble, _PHASE_CONTRACTS
from superme_agent.core.kind_profiles import KIND_PROFILES
from superme_agent.harness.tools import run_tools as RT
from superme_agent.harness.tools.dev_tools import _scaffold_artifact, _bound_err

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def test_run_protocol() -> None:
    print("run protocol — Current-focus background variant only (fence + orient retired)")
    bg = work_item_preamble("i1", {"title": "T", "phase": "plan", "kind": "implementation"},
                            "/tmp/i1", interactive=False)
    fg = work_item_preamble("i1", {"title": "T", "phase": "plan", "kind": "implementation"},
                            "/tmp/i1")
    ok("background variant carries the run protocol + the tool ending",
       "Run protocol" in bg and "report_completion" in bg and "## Assumptions" in bg)
    ok("interactive variant carries neither", "Run protocol" not in fg
       and "report_completion" not in fg)
    from superme_agent.core import kernel_speech as _ks
    ok("fence contracts + orient assembler are gone",
       not hasattr(_ks, "BACKGROUND_RUN_CONTRACT") and not hasattr(_ks, "DEPUTY_VERDICT_CONTRACT")
       and not hasattr(_ks, "render_orient_block"))


def test_report_completion_tool() -> None:
    print("report_completion tool (two-group payload → sink)")
    sink: dict = {}
    call = RT._report_completion(completion_sink=sink)
    r = asyncio.run(call({"machine": {"outcome": "success"},
                          "user": {"summary": "built it", "next": "owner reviews"}}))
    ok("valid call ok + minimal result", not r.get("is_error")
       and r["content"][0]["text"] == "ok")
    ok("sink carries legacy top-level keys + both groups",
       sink["report"]["outcome"] == "success" and sink["report"]["summary"] == "built it"
       and sink["report"]["machine"]["outcome"] == "success"
       and sink["report"]["user"]["next"] == "owner reviews")
    bad = asyncio.run(call({"machine": {"outcome": "maybe"},
                            "user": {"summary": "s", "next": "n"}}))
    ok("invalid outcome rejected", bad.get("is_error") is True)
    ok("summary/next required", asyncio.run(call(
        {"machine": {"outcome": "success"}, "user": {"summary": "s"}})).get("is_error") is True)
    ok("needs_user requires questions", asyncio.run(call(
        {"machine": {"outcome": "needs_user"},
         "user": {"summary": "s", "next": "n"}})).get("is_error") is True)
    q4 = {"question": "q?", "recommend": "do X", "why": "cheapest", "instead": "Y, if Z"}
    ok("questions only with needs_user", asyncio.run(call(
        {"machine": {"outcome": "success"},
         "user": {"summary": "s", "next": "n", "questions": [q4]}})).get("is_error") is True)
    # The four-field shape is the tool's job: prose in place of an object is refused there.
    ok("a prose question is refused", asyncio.run(call(
        {"machine": {"outcome": "needs_user"},
         "user": {"summary": "s", "next": "n",
                  "questions": ["q? — recommended: X"]}})).get("is_error") is True)
    ok("a question without a recommendation is refused", asyncio.run(call(
        {"machine": {"outcome": "needs_user"},
         "user": {"summary": "s", "next": "n",
                  "questions": [{"question": "q?"}]}})).get("is_error") is True)
    good_q = asyncio.run(call({"machine": {"outcome": "needs_user"},
                               "user": {"summary": "s", "next": "n", "questions": [q4]}}))
    ok("needs_user + typed questions accepted", not good_q.get("is_error")
       and sink["report"]["user"]["questions"] == [q4])
    # `instead` is the only optional field.
    ok("instead is optional", not asyncio.run(call(
        {"machine": {"outcome": "needs_user"},
         "user": {"summary": "s", "next": "n",
                  "questions": [{"question": "q?", "recommend": "X", "why": "w"}]}})).get("is_error"))


def test_preamble() -> None:
    print("thin (kind,phase) preamble")
    all_phases = {p for prof in KIND_PROFILES.values() for p in prof.phases}
    ok("contract table covers every phase", set(_PHASE_CONTRACTS) == all_phases,
       str(all_phases ^ set(_PHASE_CONTRACTS)))
    for phase, c in _PHASE_CONTRACTS.items():
        pre = work_item_preamble("i1", {"title": "T", "phase": phase, "kind": "implementation"},
                                 "/tmp/i1")
        assert f"superme-dev:{c['skill']}" in pre, phase
        assert "Gates:" in pre and "never you" in pre, phase
    ok("every phase names its skill + the gate rule", True)
    with_wt = work_item_preamble("i1", {"phase": "build", "git_worktree": "/tmp/wt"}, "/tmp/i1")
    without = work_item_preamble("i1", {"phase": "plan"}, "/tmp/i1")
    ok("edit boundary swaps with the worktree",
       "git worktree `/tmp/wt/`" in with_wt and "touches no real code" in without)
    ok("preamble stays thin (per-turn cost)", len(with_wt) < 1800, str(len(with_wt)))


def test_tool_scoping(tmp: Path) -> None:
    print("worker tool-scoping (own-item only)")
    ok("unbound session refused", "no bound item" in _bound_err("a", None))
    ok("cross-item refused", "bound to work-item" in _bound_err("a", "b"))
    ok("own item allowed", _bound_err("a", "a") is None)
    dev = DevKnowledgeService()
    root = tmp / "scoperoot"
    mine = dev.create_work_item(root, "mine", kind="implementation")["id"]
    other = dev.create_work_item(root, "other", kind="implementation")["id"]
    tool = _scaffold_artifact(store=None, context_id="t", dev_root=root, bound_item_id=mine)
    r = asyncio.run(tool({"item_id": other, "artifact": "plan"}))
    ok("real factory refuses cross-item", "cross-item" in json.dumps(r))
    r = asyncio.run(tool({"item_id": mine, "artifact": "plan"}))
    ok("real factory serves own item", "scaffolded" in json.dumps(r))


def test_auto_checkpoint(tmp: Path) -> None:
    print("session-end auto-checkpoint")
    from superme_agent.daemon.services.runs import bank_auto_checkpoint
    dev = DevKnowledgeService()
    internal = tmp / "acp-knowledge"
    root = internal / "dev"
    iid = dev.create_work_item(root, "checkpointed", kind="implementation")["id"]
    item_dir = root / "work-items" / iid
    (item_dir / "artifacts" / "plan.md").write_text("## Tasks\n- [x] done one\n- [ ] open one\n")
    ctx = SimpleNamespace(internal_root=internal, cwd=tmp, id="t")
    session_start = time.time() - 5
    ok("banks a fallback checkpoint", bank_auto_checkpoint(ctx, iid, since=session_start) is True)
    cp = A.latest_checkpoint(item_dir)
    ok("derived content (phase + open tasks + AUTO note)",
       "triage phase" in cp["text"] and "open one" in cp["text"] and "AUTO checkpoint" in cp["text"])
    # A checkpoint newer than the session start (the agent's own) suppresses the fallback.
    ok("defers to the agent's own checkpoint",
       bank_auto_checkpoint(ctx, iid, since=time.time() - 60) is False)
    dev.set_work_item_terminal(root, iid, "completed")
    ok("terminal item skipped", bank_auto_checkpoint(ctx, iid) is False)


def test_run_outcome(tmp: Path) -> None:
    print("spine persists run outcome")
    from superme_agent.core.spine import SystemSpine
    sp = SystemSpine(db_path=tmp / "s5.db")
    rid = sp.start_item_run("repoX", feature="plan", item_id="i1", model="m")
    sp.finish_item_run("repoX", "i1", outcome="success")
    with sp._conn() as c:
        row = c.execute("SELECT outcome, status FROM run WHERE id=?", (rid,)).fetchone()
    ok("outcome on the finished row", row["outcome"] == "success" and row["status"] == "done")
    rid2 = sp.start_item_run("repoX", feature="chat", item_id="i1")
    sp.finish_item_run("repoX", "i1")
    with sp._conn() as c:
        row = c.execute("SELECT outcome FROM run WHERE id=?", (rid2,)).fetchone()
    ok("interactive run stays outcome-NULL", row["outcome"] is None)


def test_session_count_excludes_agent_threads(tmp: Path) -> None:
    print("session_count counts conversations, not agent threads")
    from superme_agent.core.kind_profiles import is_conversation
    from superme_agent.core.spine import SystemSpine
    sp = SystemSpine(db_path=tmp / "s5count.db")
    # One of every kind the owner can open, the two headless ones, and a legacy NULL-kind row.
    for i, kind in enumerate(("general", "intake", "onboarding", "diagnosis", "build", "vet")):
        sp.record_session(f"s{i}", "/tmp", mode="dev", repo_id="repoC")
        sp.stamp_session_kind(f"s{i}", kind)
    sp.record_session("s9", "/tmp", mode="dev", repo_id="repoC")   # legacy, kind NULL
    ok("build/vet excluded, legacy NULL kept", sp.session_count("repoC", "dev") == 5,
       str(sp.session_count("repoC", "dev")))
    ok("include_agent_threads gives the true total",
       sp.session_count("repoC", "dev", include_agent_threads=True) == 7)
    ok("an unregistered kind reads as a conversation (it self-flags, never vanishes)",
       is_conversation("some_future_kind") and not is_conversation("vet"))


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_run_protocol()
        test_report_completion_tool()
        test_preamble()
        test_tool_scoping(tmp)
        test_auto_checkpoint(tmp)
        test_run_outcome(tmp)
        test_session_count_excludes_agent_threads(tmp)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
