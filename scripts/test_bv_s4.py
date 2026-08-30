"""Vet session mechanics, and the refusals that make a verdict mean something.

A verdict without evidence, or contradicting the ledger, is refused. Vet's file-writes are dead
outright, while its shell keeps autonomy to run checks.

Run: PYTHONPATH=. python -m scripts.test_bv_s4
"""

import asyncio
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from superme_agent.core import artifacts as A
from superme_agent.core.permissions import VET_READONLY_NUDGE, build_can_use_tool
from superme_agent.core.kernel_speech import work_item_preamble

PASS = 0

PLAN = """---
artifact: plan
---
# Plan — t

## Approach
x

## Tasks
- [x] a

## Inner checks
- `pytest -q`

## Vet plan
depth: checks
reason: two contained checks cover the surface
env: none

### alpha-check
- traces: d-x
- mode: command
- scenario: run the alpha suite
- expect: pytest exits 0 with exactly 3 passed and no skips

### beta-check
- traces: d-x
- mode: inspection
- scenario: read the module
- expect: module.py defines beta() returning the literal string 'beta'
"""


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def make_item(tmp: Path, name: str) -> Path:
    d = tmp / name
    (d / "artifacts").mkdir(parents=True)
    (d / "item.md").write_text("---\nid: x\n---\n", encoding="utf-8")
    (d / "artifacts" / "plan.md").write_text(PLAN, encoding="utf-8")
    return d


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def _lenses(d) -> None:
    """The three standing lenses, owed on every cycle before the report will write."""
    for ln in A.STANDING_LENSES:
        A.record_lens(d, lens=ln, probed="read the diff through this lens")

def test_report_machinery(tmp: Path, repo: Path) -> None:
    print("cycle-report machinery (fence entries + derived report + refusals)")
    d = make_item(tmp, "item-report")

    ok("no cycle reports yet", A.latest_cycle_report(d) is None)

    # Report refused while nothing (or not everything) is recorded.
    try:
        A.write_vet_user_report(d, repo)
        ok("report refused with nothing recorded", False)
    except ValueError as e:
        ok("report refused with nothing recorded", "no recorded entry" in str(e), str(e))

    # Recording refuses invented / glued check ids (join-key integrity).
    try:
        A.record_verification(d, repo, check="ghost-check", how="h", result="r", passed=True)
        ok("invented check id refused", False)
    except ValueError as e:
        ok("invented check id refused", "not a vet-plan check id" in str(e), str(e))
    try:
        A.record_verification(d, repo, check="alpha-check — the alpha suite", how="h",
                              result="r", passed=True)
        ok("glued check id refused", False)
    except ValueError as e:
        ok("glued check id refused", "glues a description" in str(e), str(e))

    e1 = A.record_verification(d, repo, check="alpha-check", how="pytest -q",
                               result="3 passed", passed=True)
    ok("first record scaffolds cycle 1 and lands in its fence",
       e1["cycle"] == 1 and (d / "artifacts" / "build-vet-1.md").exists()
       and "```checks" in (d / "artifacts" / "build-vet-1.md").read_text(encoding="utf-8"))
    try:
        A.write_vet_user_report(d, repo)
        ok("report still refused while a plan check is unrecorded", False)
    except ValueError as e:
        ok("report still refused while a plan check is unrecorded",
           "beta-check" in str(e), str(e))
    A.record_verification(d, repo, check="beta-check", how="read module.py",
                          result="beta() returns 'BETA' uppercase", passed=False,
                          note="expected 'beta', got 'BETA'")
    # Every failing check owes a diagnosis before the report will write.
    A.record_diagnosis(d, check="beta-check", where="module.py:8",
                       why="beta() upper-cases its return value")

    _lenses(d)
    r1 = A.write_vet_user_report(d, repo, summary="beta is still wrong",
                                 confirms="- alpha holds", looked_at="- Intent: read the diff against the brief.")
    text = Path(r1["path"]).read_text(encoding="utf-8")
    ok("the verdict is derived from the fence, never asserted by vet",
       r1["verdict"] == "failed" and r1["failed"] == ["beta-check"])
    ok("…and the failing check is MACHINE-authored into the report, whatever vet wrote",
       "## What didn't hold" in text and "beta-check" in text
       and "module.py:8" in text, text[:400])
    ok("…while vet's own narrative is carried verbatim",
       "beta is still wrong" in text and "- alpha holds" in text
       and "read the diff against the brief" in text)
    ok("the passing check is NOT re-listed — the Task tab carries the per-check evidence",
       "alpha-check" not in text)

    # Driver closes cycle 1; next scaffold opens cycle 2; fix lands and the report flips.
    A.append_cycle_outcome(d, evidence="failed", decision="build", reason="beta red")
    cy2 = A.scaffold_cycle(d, title="t")
    ok("outcome closes the cycle → next scaffold is cycle 2", cy2["cycle"] == 2)
    A.record_verification(d, repo, check="beta-check", how="read module.py",
                          result="beta() returns 'beta'", passed=True)
    _lenses(d)
    r2 = A.write_vet_user_report(d, repo, summary="all green now")
    text2 = Path(r2["path"]).read_text(encoding="utf-8")
    ok("the re-written report drops the didn't-hold block once nothing is red",
       r2["failed"] == [] and "What didn't hold" not in text2, text2[:400])
    ok("…and the ✗→✓ history survives where the Task tab reads it",
       [h["passed"] for r_ in A.proof_rows(d) for v in r_["verified"]
        if v["check"] == "beta-check" for h in v["history"]] == [False, True])
    latest = A.latest_cycle_report(d)
    ok("latest cycle report = newest cycle", latest["cycle"] == 2)
    ok("char cap honored", A.latest_cycle_report(d, char_cap=10)["truncated"] is True)


def _decide(fn, tool: str, args: dict):
    return asyncio.run(fn(tool, args, None))


def test_readonly_permissions(tmp: Path) -> None:
    print("vet read-only permission layer")
    wt = tmp / "wt"
    wt.mkdir()
    prompts: list[str] = []

    async def approve(tool, args):
        prompts.append(tool)
        return True

    fn = build_can_use_tool(approve, cwd=wt, write_boundary=[wt],
                            deny_write_tools=VET_READONLY_NUDGE)
    r = _decide(fn, "Write", {"file_path": str(wt / "f.py"), "content": "x"})
    ok("Write denied even INSIDE the freeze boundary",
       type(r).__name__ == "PermissionResultDeny" and VET_READONLY_NUDGE in r.message)
    r = _decide(fn, "Edit", {"file_path": str(wt / "f.py"), "old_string": "a", "new_string": "b"})
    ok("Edit denied", type(r).__name__ == "PermissionResultDeny")
    ok("no human prompt was involved", prompts == [])
    r = _decide(fn, "Bash", {"command": "cat f.py"})
    ok("read-only Bash still auto-allows", type(r).__name__ == "PermissionResultAllow")
    r = _decide(fn, "Bash", {"command": "pytest -q"})
    ok("mutating Bash inside the boundary still auto-allows (running checks IS the job)",
       type(r).__name__ == "PermissionResultAllow")
    # Control: without the flag, the boundary auto-allows the same Write.
    fn2 = build_can_use_tool(approve, cwd=wt, write_boundary=[wt])
    r = _decide(fn2, "Write", {"file_path": str(wt / "f.py"), "content": "x"})
    ok("without the flag the boundary allows in-boundary writes (build unchanged)",
       type(r).__name__ == "PermissionResultAllow")


def test_tool(tmp: Path, repo: Path) -> None:
    print("file_vet_report MCP tool")
    from superme_agent.harness.tools.dev_tools import _file_vet_report
    dev_root = tmp / "devroot"
    d = dev_root / "work-items" / "it1"
    (d / "artifacts").mkdir(parents=True)
    (d / "item.md").write_text("---\nid: it1\n---\n", encoding="utf-8")
    (d / "artifacts" / "plan.md").write_text(PLAN, encoding="utf-8")
    A.record_verification(d, repo, check="alpha-check", how="pytest -q",
                          result="3 passed", passed=True)

    events: list[tuple] = []
    store = SimpleNamespace(log_event=lambda *a, **k: events.append((a, k)))
    tool = _file_vet_report(store=store, context_id="c", dev_root=dev_root,
                            repo_dir=repo, bound_item_id="it1")

    r = asyncio.run(tool({"item_id": "other"}))
    ok("cross-item call refused", r.get("is_error"))
    r = asyncio.run(tool({"item_id": "it1"}))
    ok("tool surfaces the mechanical refusal (unrecorded plan check)",
       r.get("is_error") and "beta-check" in r["content"][0]["text"], str(r))
    A.record_verification(d, repo, check="beta-check", how="read",
                          result="'beta' ok", passed=True)
    _lenses(d)
    r = asyncio.run(tool({"item_id": "it1", "observations": "none real"}))
    ok("happy path writes reports/report-vet.md + logs the event",
       not r.get("is_error") and (d / "reports" / "report-vet.md").exists()
       and events and events[0][0][1] == "vet.report", str(r))
    ok("tool tells vet its job is done (no fixing)",
       "do not attempt fixes" in r["content"][0]["text"])


def test_reset_vet_thread(tmp: Path) -> None:
    print("reset_vet_thread (vet forgets)")
    from superme_agent.daemon.services.runs import reset_vet_thread
    deleted: list[tuple] = []
    cleared: list[tuple] = []
    sessions = SimpleNamespace(delete=lambda ctx, sid, cause: deleted.append((sid, cause)))
    dev = SimpleNamespace(set_work_item_session=lambda root, iid, sid, slot: cleared.append((iid, sid, slot)))
    ctx = SimpleNamespace(internal_root=tmp)

    ok("cycle 1 (no vet slot) is a no-op",
       reset_vet_thread(ctx, {"id": "i1", "sessions": {"plan": "s-p"}},
                        dev=dev, sessions=sessions) is False and not deleted)
    ok("re-entry retires the previous vet thread + clears the slot",
       reset_vet_thread(ctx, {"id": "i1", "sessions": {"vet": "s-v1", "build": "s-b"}},
                        dev=dev, sessions=sessions) is True
       and deleted == [("s-v1", "retired")] and cleared == [("i1", None, "vet")])
    ok("other phases' threads untouched", all(s[0] != "s-b" for s in deleted))


def test_preamble_and_registration() -> None:
    print("preamble + registration")
    item = {"title": "t", "phase": "vet", "kind": "implementation", "git_worktree": "/wt"}
    p = work_item_preamble("i1", item, "/items/i1")
    ok("vet preamble states the read-only contract",
       "read-only" in p and "file_vet_report" in p and "never fix it" in p, p[:400])
    build_p = work_item_preamble("i1", {**item, "phase": "build"}, "/items/i1")
    ok("build preamble unchanged (owns its worktree)", "all code changes happen" in build_p)
    from superme_agent.harness.tools.dev_tools import ITEM_DEV_TOOLS
    ok("file_vet_report registered", "file_vet_report" in {t.name for t in ITEM_DEV_TOOLS})
    from superme_agent.harness.policy import SAFE_TOOLS
    ok("file_vet_report auto-allows (vet's only pen — a prompt would park background cycles)",
       "mcp__dev__file_vet_report" in SAFE_TOOLS)


def test_scope_vs_ops() -> None:
    """Every authorization scope is the owner's. Reconciling a doc to what shipped is not one."""
    print("authorization scopes")
    from superme_agent.core import artifacts as A
    ok("only intent-changing scopes exist",
       set(A.AUTH_SCOPES) == {"prd-identity", "roadmap-scope", "new-decision", "doc-delete"})
    ok("the sync-to-reality scopes are gone — close writes those, asking nobody",
       not {"doc-sync", "rename-to-shipped", "roadmap-mark-done"} & set(A.AUTH_SCOPES))
    ok("nothing survives to split delegable from reserved",
       not any(hasattr(A, n) for n in ("DELEGABLE_SCOPES", "intent_ops", "scope_mismatch")))


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo = make_repo(tmp)
        test_report_machinery(tmp, repo)
        test_readonly_permissions(tmp)
        test_tool(tmp, repo)
        test_reset_vet_thread(tmp)
        test_preamble_and_registration()
        test_scope_vs_ops()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
