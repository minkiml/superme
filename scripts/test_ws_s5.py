"""WS-S5 gate test (unit half) — phase-session contract & continuity (PRD stage S5).

Covers, without a daemon or LLM: the orient block renders deterministically with fixed section
order + per-field caps (long plan truncated, checkpoint banner, gate line per status, pointers);
the completion-report contract round-trips (instructions ↔ parser) and rejects invalid outcomes;
the thin (kind,phase) preamble covers every phase and swaps edit boundaries with the worktree;
worker tool-scoping refuses cross-item and unbound calls on the REAL tool factories; the
session-end auto-checkpoint banks a fallback and defers to the agent's own; the spine persists a
run outcome. The LIVE half (real triage turn + headless plan on the dummy repo, orient-once in
the transcript) runs separately.

Run: PYTHONPATH=. python -m scripts.test_ws_s5
"""

import asyncio
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from superme_agent.core import artifacts as A
from superme_agent.core import session_contract as SC
from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.daemon.session_agents import work_item_preamble, _PHASE_CONTRACTS
from superme_agent.core.kind_profiles import KIND_PROFILES
from superme_agent.harness.tools.dev_tools import _scaffold_artifact, _bound_err

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def test_orient(tmp: Path) -> None:
    print("orient block (D11 §3)")
    dev = DevKnowledgeService()
    root = tmp / "devroot"
    iid = dev.create_work_item(root, "Orient me", "the description")["id"]
    item_dir = root / "work-items" / iid
    item = dev.read_work_item(root, iid)

    b1 = SC.render_orient_block(item, item_dir)
    b2 = SC.render_orient_block(item, item_dir)
    ok("deterministic (same input → same output)", b1 == b2)
    order = [b1.find(s) for s in ("#### Plan", "#### Latest checkpoint", "#### Gate",
                                  "#### Where things live")]
    ok("fixed section order", all(x >= 0 for x in order) and order == sorted(order), str(order))
    ok("no plan / no checkpoint placeholders",
       "no plan.md yet" in b1 and "no checkpoint banked yet" in b1)
    ok("gate names the next phase", "`triage` → `plan`" in b1, b1)

    # Plan cap: a huge plan is truncated at the cap.
    (item_dir / "artifacts").mkdir(exist_ok=True)
    (item_dir / "artifacts" / "plan.md").write_text("x" * 20_000)
    b = SC.render_orient_block(item, item_dir)
    ok("plan capped", "… (truncated)" in b and len(b) < 12_000, str(len(b)))

    # Checkpoint appears with the data-not-instructions banner.
    A.write_checkpoint(item_dir, tmp, working_on="w", decisions="d", remaining="r")
    b = SC.render_orient_block(item, item_dir)
    ok("checkpoint + verify banner", "DATA from a previous session" in b and "working_on" not in b)

    # awaiting_human flips the gate line; children + worktree + preliminary render.
    dev.set_work_item_status(root, iid, "awaiting_human")
    (item_dir / "preliminary").mkdir()
    item = dev.read_work_item(root, iid)
    item["git_worktree"] = "/tmp/wt"
    item["git_branch"] = "item/x"
    kids = [{"id": "k1", "status": "active"}]
    b = SC.render_orient_block(item, item_dir, children=kids)
    ok("awaiting gate line", "PARKED AWAITING THE OWNER" in b)
    ok("children + git + preliminary + worktree pointer",
       "`k1` active" in b and "item/x" in b and "preliminary/" in b
       and "Your working tree" in b)


def test_completion_contract() -> None:
    print("completion report (both directions)")
    instr = SC.completion_report_instructions()
    ok("instructions name the fence + outcomes",
       "```completion-report" in instr and all(o in instr for o in SC.RUN_OUTCOMES))
    text = ("did stuff\n```completion-report\noutcome: success\nsummary: built it\n"
            "next: owner reviews\n```\n")
    r = SC.parse_completion_report(text)
    ok("round-trip parse", r == {"outcome": "success", "summary": "built it",
                                 "next": "owner reviews"}, str(r))
    two = text + "\n```completion-report\noutcome: blocked\nsummary: later\nnext: fix\n```"
    ok("last fence wins", SC.parse_completion_report(two)["outcome"] == "blocked")
    ok("invalid outcome rejected",
       SC.parse_completion_report("```completion-report\noutcome: maybe\n```") is None)
    ok("missing report → None", SC.parse_completion_report("all done!") is None)
    ok("empty → None", SC.parse_completion_report(None) is None)


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
    mine = dev.create_work_item(root, "mine")["id"]
    other = dev.create_work_item(root, "other")["id"]
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
    iid = dev.create_work_item(root, "checkpointed")["id"]
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


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_orient(tmp)
        test_completion_contract()
        test_preamble()
        test_tool_scoping(tmp)
        test_auto_checkpoint(tmp)
        test_run_outcome(tmp)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
