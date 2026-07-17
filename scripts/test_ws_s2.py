"""WS-S2 gate test — artifact machinery (workspace-workflow PRD stage S2).

Covers the PRD gate: scaffold every artifact kind → fill → self-check passes; placeholder text
fails the check; an edit after a green evidence entry flips the ledger stale; a closeout claiming
a nonexistent file / fake commit is rejected with no state change; checkpoints are append-only and
ordered; tasks parse from plan.md's `## Tasks`; the computed artifact-status map derives correctly.
Self-cleaning (tempdirs + a scratch git repo). No daemon needed.

Run: PYTHONPATH=. python -m scripts.test_ws_s2
"""

import re
import subprocess
import tempfile
from pathlib import Path

from superme_agent.core import artifacts as A
from superme_agent.core.dev_knowledge import DevKnowledgeService

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def fill_all(path: Path, filler=lambda m: "real content") -> None:
    """Replace every <fill:…> slot with content."""
    text = path.read_text()
    path.write_text(A.FILL.sub("filled — real content here", text))


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert r.returncode == 0, f"git {args}: {r.stderr}"
    return r.stdout.strip()


def make_repo(tmp: Path) -> Path:
    repo = tmp / "scratch-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("print('hi')\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "init")
    return repo


def test_scaffold_and_check(item: Path) -> None:
    print("scaffold → fill → self-check (every kind)")
    try:
        A.scaffold(item, "bogus")
        ok("unknown artifact fails loud", False)
    except KeyError:
        ok("unknown artifact fails loud", True)

    for kind, item_kind in [("plan", "implementation"), ("plan", "research"),
                            ("validation", None), ("readiness", None),
                            ("findings", None), ("closeout", None), ("handoff-brief", None)]:
        d = item / f"case-{kind}-{item_kind or 'x'}"
        r = A.scaffold(d, kind, title="T", item_kind=item_kind, item_id="i1")
        assert r["created"], kind
        # Fresh scaffold must FAIL the check (unfilled slots) — except none? all have fills.
        issues = A.self_check(d, kind, item_kind=item_kind)
        assert issues, f"{kind}: fresh scaffold should not pass ({issues})"
        p = Path(r["path"])
        fill_all(p)
        if kind == "closeout":  # facts yaml keys survive fill (fenced block has no fills)
            pass
        issues = A.self_check(d, kind, item_kind=item_kind)
        assert not issues, f"{kind}: filled doc should pass, got {issues}"
        # Re-scaffold = no-op, never overwrite.
        r2 = A.scaffold(d, kind, title="T", item_kind=item_kind)
        assert not r2["created"] and "real content" in p.read_text()
    ok("all kinds scaffold, reject unfilled, pass when filled, never overwrite", True)

    # Required-section deletion is caught.
    d = item / "case-missing-section"
    p = Path(A.scaffold(d, "readiness", title="T")["path"])
    fill_all(p)
    p.write_text(p.read_text().replace("## Warnings", "## Warn-ish"))
    issues = A.self_check(d, "readiness")
    ok("missing required section caught", any("Warnings" in i for i in issues), str(issues))

    # Research plan requires its own sections.
    d = item / "case-research-plan"
    p = Path(A.scaffold(d, "plan", title="T", item_kind="research")["path"])
    assert "## Boundaries" in p.read_text()
    ok("plan template is item-kind-parameterized", True)


def test_evidence(item: Path, repo: Path) -> None:
    print("evidence ledger (stale-on-edit)")
    d = item / "case-evidence"
    try:
        A.record_evidence(d, repo, check="x", how="", result="y", passed=True)
        ok("empty evidence fields rejected", False)
    except ValueError:
        ok("empty evidence fields rejected", True)
    A.record_evidence(d, repo, check="unit tests", how="pytest -q", result="42 passed", passed=True)
    st = A.evidence_status(d, repo)
    ok("green + fresh → passed", st["status"] == "passed", str(st))
    # Edit the repo → stale.
    (repo / "a.py").write_text("print('changed')\n")
    st = A.evidence_status(d, repo)
    ok("repo edit flips stale", st["status"] == "stale", str(st))
    # Re-run the check green again → passed again.
    A.record_evidence(d, repo, check="unit tests", how="pytest -q", result="42 passed", passed=True)
    st = A.evidence_status(d, repo)
    ok("re-run restores passed", st["status"] == "passed", str(st))
    # A failing latest entry → failed (beats staleness).
    A.record_evidence(d, repo, check="parity", how="parity check", result="exit 1", passed=False)
    st = A.evidence_status(d, repo)
    ok("failing check → failed", st["status"] == "failed" and st["failed_checks"] == ["parity"], str(st))
    ok("entries parse back", len(A.evidence_entries(d)) == 3)
    git(repo, "checkout", "--", "a.py")  # restore for closeout test


def test_checkpoints(item: Path, repo: Path) -> None:
    print("checkpoints (append-only)")
    d = item / "case-checkpoints"
    try:
        A.write_checkpoint(d, repo, working_on="", decisions="", remaining="x")
        ok("empty checkpoint rejected", False)
    except ValueError:
        ok("empty checkpoint rejected", True)
    p1 = A.write_checkpoint(d, repo, working_on="stage S2", decisions="chose yaml facts",
                            remaining="wire tools", notes="tried X, failed")
    p2 = A.write_checkpoint(d, repo, working_on="stage S2 later", decisions="",
                            remaining="gate test")
    ok("append-only two files", p1 != p2 and len(list((d / 'checkpoints').glob('*.md'))) == 2)
    latest = A.latest_checkpoint(d)
    ok("latest = newest by filename", latest["path"] == p2 and "later" in latest["text"])
    ok("git-state header present", re.search(r"git: .+ @ ", Path(p1).read_text()) is not None)
    cap = A.latest_checkpoint(d, char_cap=20)
    ok("char cap honored", len(cap["text"]) == 20 and cap["truncated"])


def test_closeout_verify(item: Path, repo: Path) -> None:
    print("closeout ground-truth verification")
    d = item / "case-closeout"
    p = Path(A.scaffold(d, "closeout", title="T", item_id="i9")["path"])
    head = git(repo, "rev-parse", "HEAD")
    # Honest closeout: real file, real commit, real artifact path.
    good = p.read_text()
    good = A.FILL.sub("delivered the thing", good)
    good = good.replace("changed_files: []", "changed_files:\n  - a.py")
    good = good.replace('merge_commit: ""', f"merge_commit: {head}")
    good = good.replace("<fill:bullet list of this item's artifact paths worth keeping, or \"none\">",
                        "- artifacts/closeout.md")
    p.write_text(good)
    okv, issues = A.verify_closeout(d, repo)
    ok("honest closeout verifies", okv, str(issues))
    # Fabricated file claim → rejected, file untouched (no state change).
    before = p.read_text()
    bad = before.replace("- a.py", "- totally/made_up.py")
    p.write_text(bad)
    okv, issues = A.verify_closeout(d, repo)
    ok("fabricated changed-file rejected", not okv and any("made_up.py" in i for i in issues), str(issues))
    ok("verification changed no state", p.read_text() == bad)  # read-only verify
    p.write_text(before)
    # Fake commit → rejected.
    p.write_text(before.replace(head, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"))
    okv, issues = A.verify_closeout(d, repo)
    ok("fake merge_commit rejected", not okv and any("merge_commit" in i for i in issues), str(issues))
    # Broken facts yaml → itemized issue.
    p.write_text(before.replace("changed_files:", "changed_files: ["))
    okv, issues = A.verify_closeout(d, repo)
    ok("broken facts yaml rejected", not okv, str(issues))


def test_tasks_and_status(tmp: Path, repo: Path) -> None:
    print("tasks-in-plan + computed artifact status")
    dev = DevKnowledgeService()
    root = tmp / "devroot"
    wid = dev.create_work_item(root, "s2 item")["id"]
    item_dir = root / "work-items" / wid
    p = Path(A.scaffold(item_dir, "plan", title="s2 item", item_kind="implementation")["path"])
    text = A.FILL.sub("filled", p.read_text())
    text = text.replace("- [ ] filled", "- [x] step one\n- [ ] step two\n- [ ] step three")
    p.write_text(text)
    ok("progress from plan.md ## Tasks", dev.task_progress(root, wid) == {"done": 1, "total": 3})
    tasks = dev.read_tasks(root, wid)
    ok("structured tasks parse", tasks[0] == {"text": "step one", "done": True} and len(tasks) == 3)

    item = dev.read_work_item(root, wid)
    st = A.artifact_status(item, item_dir, repo)
    ok("plan ok / others missing",
       st["plan"]["status"] == "ok" and st["validation"]["status"] == "missing"
       and st["closeout"]["required"] and st["findings"]["required"] is False, str(st))
    A.record_evidence(item_dir, repo, check="c", how="h", result="r", passed=True)
    st = A.artifact_status(item, item_dir, repo)
    ok("validation carries evidence verdict", st["validation"]["evidence"]["status"] == "passed")

    # Legacy fallback: an old item with only tasks.md still reads.
    wid2 = dev.create_work_item(root, "legacy item")["id"]
    (root / "work-items" / wid2 / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "work-items" / wid2 / "artifacts" / "tasks.md").write_text("- [x] a\n- [ ] b\n")
    ok("legacy tasks.md fallback", dev.task_progress(root, wid2) == {"done": 1, "total": 2})


def test_tool_registration() -> None:
    print("tool registration")
    from superme_agent.harness.tools.dev_tools import DEV_TOOLS, _ITEM_DEV_TOOLS
    names = {t.name for t in _ITEM_DEV_TOOLS}
    ok("S2 tools registered",
       names == {"scaffold_artifact", "record_validation_evidence", "write_checkpoint",
                "sync_from_main",                            # joined in S4
                "stage_knowledge_delta", "propose_close",    # joined in S6
                "set_triage_classification"}                 # joined in the audit batch (R6)
       and all(t in {x.name for x in DEV_TOOLS} for t in names))


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo = make_repo(tmp)
        item = tmp / "items"
        test_scaffold_and_check(item)
        test_evidence(item, repo)
        test_checkpoints(item, repo)
        test_closeout_verify(item, repo)
        test_tasks_and_status(tmp, repo)
        test_tool_registration()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
