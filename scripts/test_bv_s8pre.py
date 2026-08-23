"""Four fixes a live walk-through surfaced, all checkable offline.

A resume naming an unknown session mints one, `triage_ran` reads a stamp, merge is phase-gated,
and background runs mount dev tools.

Run: PYTHONPATH=. python -m scripts.test_bv_s8pre
"""

import asyncio
import re
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.core import gate_briefs as GB
from scripts.sources import src

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# ------------------------------------------------------------------ dangling resume
def test_f0_dangling_resume() -> None:
    print("F0 — dangling msg.resume mints fresh")
    from superme_agent.daemon.routers.ws import _live_resume
    ok("dangling resume (sid, no row) → None", _live_resume("sess-1", None) is None)
    ok("live resume (sid + row) → the sid", _live_resume("sess-1", {"id": "sess-1"}) == "sess-1")
    ok("no resume → None", _live_resume(None, None) is None)
    ws_src = _norm(src("superme_agent/daemon/routers/ws.py"))
    ok("ws turn path wires the guard",
       "turn_resume = _live_resume(msg.resume, resumed)" in ws_src)


# ------------------------------------------------------------------ triaged_at stamp
def test_f1_triaged_stamp(tmp: Path) -> None:
    print("F1 — triage_ran reads the triaged_at stamp")
    dev = DevKnowledgeService()
    root = tmp / "devroot-f1"
    wid = dev.create_work_item(root, "f1 item", kind="implementation")["id"]
    item = dev.read_work_item(root, wid)
    ok("fresh item carries no stamp", not item.get("triaged_at"))

    # Setter: first stamp wins, idempotent, missing item is a no-op.
    ok("setter stamps", dev.set_work_item_triaged(root, wid) is True)
    item = dev.read_work_item(root, wid)
    ok("stamp reads back as today", str(item.get("triaged_at")) == date.today().isoformat(),
       str(item.get("triaged_at")))
    ok("second stamp is a no-op", dev.set_work_item_triaged(root, wid) is False)
    ok("missing item is a no-op", dev.set_work_item_triaged(root, "nope") is False)

    # Gate brief: kind + body filled but NO stamp → not ready (the old tautology is dead).
    wid2 = dev.create_work_item(root, "f1 tautology probe", kind="implementation")["id"]
    dev.set_work_item_kind(root, wid2, "implementation")
    item2 = dev.read_work_item(root, wid2)
    item2["description"] = "a filled body that the old check would have called triage"
    brief = GB.gate_state(item2, root / "work-items" / wid2, root, None)
    chk = next(c for c in brief["checks"] if c["criterion"] == "triage_ran")
    ok("kind+body WITHOUT stamp → triage_ran fails", chk["ok"] is False, str(chk))
    dev.set_work_item_triaged(root, wid2)
    item2 = dev.read_work_item(root, wid2)
    brief = GB.gate_state(item2, root / "work-items" / wid2, root, None)
    chk = next(c for c in brief["checks"] if c["criterion"] == "triage_ran")
    ok("stamped → triage_ran passes", chk["ok"] is True, str(chk))

    # The tool is the stamping surface: recording a classification stamps; the phase guard
    # refuses post-triage (and leaves no stamp behind).
    from superme_agent.harness.tools.dev_tools import _set_triage_classification
    store = SimpleNamespace(log_event=lambda *a, **k: None)

    def tool_for(iid: str):
        return _set_triage_classification(store=store, context_id="t", dev_root=str(root),
                                          bound_item_id=iid)

    wid3 = dev.create_work_item(root, "f1 tool probe", kind="implementation")["id"]
    # Triage names, classifies and scales in ONE call, so a classification without scale is
    # refused.
    r = asyncio.run(tool_for(wid3)({"item_id": wid3, "title": "Probe the triage stamp",
                                    "kind": "implementation", "scale": "standard",
                                    "scale_reason": "touches the run path in two places"}))
    item3 = dev.read_work_item(root, wid3)
    ok("tool call stamps triaged_at",
       not r.get("is_error") and str(item3.get("triaged_at")) == date.today().isoformat(), str(r))
    ok("and it renamed the item", str(item3.get("title")) == "Probe the triage stamp", str(item3))
    r = asyncio.run(tool_for(wid3)({"item_id": wid3, "kind": "implementation"}))
    ok("a classification with no title is refused", bool(r.get("is_error")), str(r))
    wid4 = dev.create_work_item(root, "f1 phase-guard probe", kind="implementation")["id"]
    dev.set_work_item_phase(root, wid4, "plan")
    r = asyncio.run(tool_for(wid4)({"item_id": wid4, "title": "Probe the phase guard",
                                    "kind": "implementation"}))
    item4 = dev.read_work_item(root, wid4)
    ok("post-triage tool call refused + no stamp",
       r.get("is_error") and not item4.get("triaged_at"), str(r))


# ------------------------------------------------------------------ merge phase gate
def test_f2_merge_gate(tmp: Path) -> None:
    print("F2 — the review merge is review-phase-only")
    from fastapi import HTTPException
    from superme_agent.daemon.routers.dev import git as GR

    real_contexts = GR.contexts
    items: dict[str, dict] = {}
    stub_dev = SimpleNamespace(read_work_item=lambda _root, iid: items.get(iid))
    stub_store = SimpleNamespace(log_event=lambda *a, **k: None)
    stub_spine = SimpleNamespace(is_item_running=lambda *_: False)
    ctx = SimpleNamespace(internal_root=tmp / "f2-internal", cwd=tmp / "f2-repo", id="t",
                          mode="dev")
    try:
        GR.contexts = SimpleNamespace(resolve=lambda cid, mode: ctx)

        def merge(iid: str):
            return asyncio.run(GR.dev_work_item_git_merge(
                iid, GR.GitBody(context_id="t"),
                dev=stub_dev, dev_store=stub_store, spine=stub_spine))

        for phase in ("triage", "plan", "build", "vet", "close"):
            items["i1"] = {"id": "i1", "phase": phase, "git_branch": "item/i1"}
            try:
                merge("i1")
                ok(f"merge in `{phase}` → 409", False)
            except HTTPException as e:
                ok(f"merge in `{phase}` → 409",
                   e.status_code == 409 and "review-gate action" in str(e.detail), str(e.detail))
        # AT review the gate opens and the route reaches its ordinary refusals, proving it keys on
        # phase.
        items["i1"] = {"id": "i1", "phase": "review"}
        try:
            merge("i1")
            ok("merge at review passes the phase gate", False)
        except HTTPException as e:
            ok("merge at review passes the phase gate",
               e.status_code == 409 and "no branch" in str(e.detail), str(e.detail))
    finally:
        GR.contexts = real_contexts

    fe = _norm(src("web/frontend/src/features/dev/WorkItemModal.tsx"))
    # Activation is computed once, SERVER-SIDE, so the component only READS it. The removed button
    # skipped Approve's locks.
    ok("the FE never re-derives the landing rule",
       "review_mode !== 'strict'" not in fe and "prOpen" not in fe)
    ok("...and one control performs the gate's act, not two",
       "a.id === 'merge'" not in fe and "merge: () => advanceWorkItem" not in fe)
    # A relevant control is never hidden: an absent button reads as a missing feature.
    ok("...while the git controls that remain carry the server's reason as their tooltip",
       "disabled={!pr.active}" in fe and "title={pr.reason}" in fe)
    # There is NO owner-facing freshness sync: it happens at the three moments that matter.
    ok("no owner-facing freshness sync survives on the Git tab",
       "Sync from" not in fe and "syncWorkItemGit" not in fe)


# ------------------------------------------------------------------ background dev MCP
class _FakeAgent:
    def __init__(self):
        self.kw = None

    def run_turn(self, ctx, prompt, **kw):
        self.kw = kw

        async def _gen():
            if False:
                yield None
        return _gen()


def test_f8_background_mcp(tmp: Path) -> None:
    print("F8-residual — background plan/resolve mount the dev MCP")
    from superme_agent.daemon.services.runs import background as R, lifecycle as RL
    from superme_agent.core import git_layer

    dev = DevKnowledgeService()
    ctx = SimpleNamespace(internal_root=tmp / "f8-internal", cwd=tmp / "f8-repo", id="t",
                          mode="dev")
    (ctx.internal_root / "dev").mkdir(parents=True, exist_ok=True)
    ctx.cwd.mkdir(parents=True, exist_ok=True)
    root = ctx.internal_root / "dev"
    wid = dev.create_work_item(root, "f8 item", kind="implementation")["id"]
    item_dir = root / "work-items" / wid

    # `scope` is required: a background run mounts its phase's tools, not the whole catalogue.
    mount = RL.dev_mcp(ctx, ctx.cwd, wid, scope="plan")
    ok("dev_mcp returns a dev-keyed mount", isinstance(mount, dict) and mount.get("dev"))

    saved = {n: getattr(R, n) for n in
             ("_agent", "_dev", "_spine", "_dev_store", "_sessions",
              "capture_prompt", "capture_event", "end_run",
              "bank_auto_checkpoint", "git_layer")}
    agent = _FakeAgent()
    try:
        R._agent = agent
        R._dev = dev
        R._spine = SimpleNamespace(effective_effort=lambda *a, **k: "medium",
                                   stamp_session_item=lambda *a: None,
                                   stamp_session_kind=lambda *a: None)
        R._dev_store = SimpleNamespace(log_event=lambda *a, **k: None)
        R._sessions = SimpleNamespace(record=lambda *a: None, delete=lambda *a, **k: None)
        R.capture_prompt = lambda *a, **k: None
        R.capture_event = lambda *a, **k: None
        R.end_run = lambda *a, **k: None
        R.bank_auto_checkpoint = lambda *a, **k: False
        asyncio.run(R.run_background_plan(ctx, "t", wid, item_dir))
        ok("background plan passes extra_mcp_servers",
           isinstance(agent.kw.get("extra_mcp_servers"), dict)
           and agent.kw["extra_mcp_servers"].get("dev"), str(agent.kw and agent.kw.keys()))

        agent.kw = None
        wt = tmp / "f8-worktree"
        wt.mkdir(exist_ok=True)

        def _no_merge(_wt):
            raise git_layer.GitError("no merge in progress (probe)")
        R.git_layer = SimpleNamespace(finish_merge=_no_merge, GitError=git_layer.GitError)
        asyncio.run(R.run_background_resolve(ctx, "t", wid, wt, ["a.py"]))
        ok("background resolve passes extra_mcp_servers",
           isinstance(agent.kw.get("extra_mcp_servers"), dict)
           and agent.kw["extra_mcp_servers"].get("dev"), str(agent.kw and agent.kw.keys()))
    finally:
        for n, v in saved.items():
            setattr(R, n, v)

    # The loop runners keep their own mount — this batch changed plan/resolve only.
    loop_src = src("superme_agent/daemon/services/loop.py")
    ok("loop runners still mount dev MCP", loop_src.count("**dev_mcp(") >= 2)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_f0_dangling_resume()
        test_f1_triaged_stamp(tmp)
        test_f2_merge_gate(tmp)
        test_f8_background_mcp(tmp)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
