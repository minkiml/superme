"""WS-S4 gate test — the git layer (workspace-workflow PRD stage S4 / D4).

Scripted E2E on a throwaway git repo: transactional worktree create (+ failure unwind), kill-mid-
create healed by reconciliation, commits in the worktree, freshness merge (sync_from_main), merge
to main with backup ref + tagged auto-stash verified pop, overlap refusal, never-merge-twice,
revert via backup ref (+ refusal once commits land on top), manufactured conflict → in-tree
resolution → finish_merge (the same mechanical path the Resolve-with-Agent run drives), blocking
child branches FROM parent and light-merges INTO it while the family merges to main once, the
freeze boundary denies an outside write, and terminal removal keeps the branch ref. Self-cleaning.

Run: PYTHONPATH=. python -m scripts.test_ws_s4
"""

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from superme_agent.core import git_layer as G
from superme_agent.core.permissions import build_can_use_tool

PASS = 0
RID = "proj"   # the repo ID keying the worktree home (repos.yaml id, not the folder name)


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def make_repo(tmp: Path) -> Path:
    repo = tmp / "proj"
    repo.mkdir()
    sh(repo, "git", "init", "-b", "main")
    sh(repo, "git", "config", "user.email", "t@t")
    sh(repo, "git", "config", "user.name", "t")
    (repo / "app.py").write_text("line1\nline2\nline3\n")
    (repo / "README.md").write_text("readme\n")
    sh(repo, "git", "add", "-A")
    sh(repo, "git", "commit", "-m", "init")
    return repo


def commit_all(cwd: Path, msg: str) -> None:
    sh(cwd, "git", "add", "-A")
    sh(cwd, "git", "commit", "-m", msg)


def test_lifecycle(repo: Path) -> None:
    print("worktree lifecycle")
    rec = G.create_worktree(repo, RID, "aaa111", "My Feature!")
    wt = Path(rec["worktree"])
    ok("branch name slugged", rec["branch"] == "item/aaa111-my-feature", rec["branch"])
    ok("worktree under the owned home keyed by repo id",
       wt.is_dir() and wt.parent == G.worktrees_home() / RID, str(wt))
    # The two locations we rejected: inside the repo (git clean -fdx would eat in-flight work)
    # and the old sibling `../<repo>_worktrees` (pollutes the owner's parent dir).
    ok("worktree neither in-repo nor a repo sibling",
       repo not in wt.parents and wt.parent.parent != repo.parent, str(wt))
    ok("record has base sha", rec["base"] == "main" and len(rec["base_sha"]) == 40)
    # Transactional unwind: same item again → dir exists → refuse, and NO stray second branch.
    try:
        G.create_worktree(repo, RID, "aaa111", "My Feature!")
        ok("duplicate create refused", False)
    except G.GitError:
        ok("duplicate create refused", True)
    # Commits in the worktree land on the item branch only.
    (wt / "feature.py").write_text("new\n")
    commit_all(wt, "feat")
    ok("worktree commit on branch, not main",
       "feature.py" in sh(repo, "git", "ls-tree", "--name-only", rec["branch"])
       and "feature.py" not in sh(repo, "git", "ls-tree", "--name-only", "main"))
    h = G.worktree_health(repo, RID, "aaa111", rec["branch"])
    ok("health: ok · ahead 1 · not merged",
       h["ok"] and h["ahead"] == 1 and not h["merged"] and h["registered"], str(h))
    # Kill-mid-create / deleted dir: branch survives, dir gone → reconcile re-adds it.
    import shutil
    shutil.rmtree(wt)
    acts = G.reconcile(repo, RID, {"aaa111": {"branch": rec["branch"], "worktree": rec["worktree"]}})
    ok("reconcile recreated missing dir",
       any(a["action"] == "recreated" for a in acts) and wt.is_dir(), str(acts))
    # Broken record (branch deleted) → reported, never guessed.
    G.remove_worktree(repo, RID, "aaa111")
    sh(repo, "git", "branch", "-D", rec["branch"])
    acts = G.reconcile(repo, RID, {"aaa111": {"branch": rec["branch"], "worktree": rec["worktree"]}})
    ok("reconcile reports missing branch as broken",
       any(a["action"] == "broken" for a in acts), str(acts))


def test_merge_flow(repo: Path) -> None:
    print("merge machinery (heavy path)")
    rec = G.create_worktree(repo, RID, "bbb222", "merge me")
    wt = Path(rec["worktree"])
    (wt / "merged.py").write_text("payload\n")
    commit_all(wt, "work")
    # Freshness: land an unrelated commit on main, then sync it INTO the branch.
    (repo / "other.txt").write_text("trunk moved\n")
    commit_all(repo, "trunk work")
    res = G.sync_from_main(repo, wt)
    ok("freshness merge brings trunk in",
       res["merged"] and (wt / "other.txt").exists(), str(res))
    ok("second sync is up-to-date noop", G.sync_from_main(repo, wt).get("up_to_date") is True)
    # Merge to main with an UNRELATED dirty file in main → auto-stash + verified pop.
    (repo / "scratchpad.txt").write_text("uncommitted note\n")
    res = G.merge_to_main(repo, RID, "bbb222", rec["branch"])
    ok("merged to main with backup ref",
       res["merged"] and res["backup_ref"].startswith("refs/backup/bbb222-"), str(res))
    ok("stash pop verified (dirty file survived, no warning)",
       (repo / "scratchpad.txt").exists() and not res.get("stash_warning"))
    ok("merge commit on main", "merged.py" in sh(repo, "git", "ls-tree", "--name-only", "main"))
    ok("never-merge-twice", G.merge_to_main(repo, RID, "bbb222", rec["branch"])["already_merged"] is True)
    # Revert restores the pre-merge head…
    pre = sh(repo, "git", "rev-parse", res["backup_ref"]).strip()
    (repo / "scratchpad.txt").unlink()  # clean tree for the revert
    rev = G.revert_merge(repo, res["backup_ref"])
    ok("revert restored pre-merge head",
       rev["reverted"] and sh(repo, "git", "rev-parse", "main").strip() == pre)
    # …re-merge, land a commit ON TOP → revert must refuse (safe-only window).
    res2 = G.merge_to_main(repo, RID, "bbb222", rec["branch"])
    (repo / "later.txt").write_text("landed after\n")
    commit_all(repo, "after merge")
    try:
        G.revert_merge(repo, res2["backup_ref"])
        ok("revert refused once commits landed on top", False)
    except G.GitError:
        ok("revert refused once commits landed on top", True)
    # Overlap refusal: dirty file in main that the NEXT branch also touches.
    rec2 = G.create_worktree(repo, RID, "ccc333", "overlap")
    wt2 = Path(rec2["worktree"])
    (wt2 / "app.py").write_text("line1\nbranch change\nline3\n")
    commit_all(wt2, "branch touches app.py")
    (repo / "app.py").write_text("line1\nline2\nline3\ndirty main edit\n")
    try:
        G.merge_to_main(repo, RID, "ccc333", rec2["branch"])
        ok("overlap detected → merge refused", False)
    except G.GitError as e:
        ok("overlap detected → merge refused", "app.py" in str(e), str(e))
    sh(repo, "git", "checkout", "--", "app.py")
    G.remove_worktree(repo, RID, "ccc333")


def test_conflict_resolution(repo: Path) -> None:
    print("conflict → in-tree resolution (the Resolve-with-Agent mechanical path)")
    rec = G.create_worktree(repo, RID, "ddd444", "conflict")
    wt = Path(rec["worktree"])
    (wt / "app.py").write_text("line1\nBRANCH version\nline3\n")
    commit_all(wt, "branch side")
    (repo / "app.py").write_text("line1\nMAIN version\nline3\n")
    commit_all(repo, "main side")
    # Default: conflict aborts + reports, tree left clean.
    res = G.sync_from_main(repo, wt)
    ok("conflict aborted + reported",
       not res["merged"] and res["conflicts"] == ["app.py"] and not res["in_tree"], str(res))
    ok("worktree clean after abort", G.check_git_state(wt)["ok"] and not G.check_git_state(wt)["dirty"])
    # Resolve path: leave in tree → finish_merge refuses on markers → resolve → finishes.
    res = G.sync_from_main(repo, wt, leave_conflicts=True)
    ok("conflict left in tree for resolution", res["in_tree"] and G.conflicted_files(wt) == ["app.py"])
    try:
        G.finish_merge(wt)
        ok("finish refused while markers remain", False)
    except G.GitError as e:
        ok("finish refused while markers remain", "app.py" in str(e), str(e))
    (wt / "app.py").write_text("line1\nMERGED version\nline3\n")   # the agent's resolution
    fin = G.finish_merge(wt)
    ok("finish completed the merge", fin["merged"] and not G.check_git_state(wt)["in_merge"])
    # Freshness rule payoff: the merge back to main is now trivial.
    res = G.merge_to_main(repo, RID, "ddd444", rec["branch"])
    ok("post-resolution main merge trivial", res["merged"], str(res))
    G.remove_worktree(repo, RID, "ddd444")


def test_family(repo: Path) -> None:
    print("blocking-child family (light path)")
    prec = G.create_worktree(repo, RID, "eee555", "parent")
    pwt = Path(prec["worktree"])
    (pwt / "parent.py").write_text("parent work\n")
    commit_all(pwt, "parent work")
    # Blocking child branches FROM the parent's branch → it sees parent's unmerged work.
    crec = G.create_worktree(repo, RID, "fff666", "child", base=prec["branch"])
    cwt = Path(crec["worktree"])
    ok("child based on parent branch", (cwt / "parent.py").exists() and crec["base"] == prec["branch"])
    (cwt / "child.py").write_text("child work\n")
    commit_all(cwt, "child work")
    # Light merge into the parent's branch — main untouched, no backup ceremony.
    res = G.merge_into_parent(repo, crec["branch"], pwt)
    ok("child merged into parent branch",
       res["merged"] and res["target"] == prec["branch"] and "backup_ref" not in res, str(res))
    ok("main untouched by light merge",
       "child.py" not in sh(repo, "git", "ls-tree", "--name-only", "main"))
    # Family merges to main ONCE, through the parent.
    res = G.merge_to_main(repo, RID, "eee555", prec["branch"])
    names = sh(repo, "git", "ls-tree", "--name-only", "main")
    ok("family merged to main once", res["merged"] and "parent.py" in names and "child.py" in names)
    G.remove_worktree(repo, RID, "fff666")
    # Terminal: dir removed + verified, branch ref SURVIVES.
    out = G.remove_worktree(repo, RID, "eee555")
    ok("terminal removed dir (verified)", out["removed"] and out["verified"] and not pwt.exists())
    ok("branch ref survives terminal", G.branch_exists(repo, prec["branch"]))


def test_freeze_boundary(repo: Path, tmp: Path) -> None:
    print("freeze boundary (build-phase write confinement)")
    rec = G.create_worktree(repo, RID, "ggg777", "freeze")
    wt = Path(rec["worktree"])
    item_dir = tmp / "devroot" / "work-items" / "ggg777"
    item_dir.mkdir(parents=True)

    async def never(_t, _i):  # the surface ApproveFn must NOT be consulted during a freeze
        raise AssertionError("freeze boundary escalated to approval")

    can = build_can_use_tool(never, write_boundary=[wt, item_dir])

    async def check(tool, inp):
        return await can(tool, inp, None)

    r = asyncio.run(check("Write", {"file_path": str(wt / "inside.py"), "content": "x"}))
    ok("write inside worktree auto-allows", type(r).__name__ == "PermissionResultAllow")
    r = asyncio.run(check("Edit", {"file_path": str(item_dir / "artifacts" / "plan.md")}))
    ok("write inside item dir auto-allows", type(r).__name__ == "PermissionResultAllow")
    r = asyncio.run(check("Write", {"file_path": str(repo / "app.py"), "content": "x"}))
    ok("write into MAIN repo hard-denied",
       type(r).__name__ == "PermissionResultDeny" and "boundary" in r.message)
    r = asyncio.run(check("Write", {"file_path": "/tmp/elsewhere.txt", "content": "x"}))
    ok("write anywhere else hard-denied", type(r).__name__ == "PermissionResultDeny")
    r = asyncio.run(check("Write", {"content": "no path at all"}))
    ok("pathless write fails closed", type(r).__name__ == "PermissionResultDeny")

    # Bash under the boundary (F9): a phase agent in its own worktree owns its shell — tests,
    # installs and commits must not park on a human, or the build⟷validate loop can't run.
    # `never` still guards the escalation path: an auto-allow that regressed to a prompt raises.
    can_sh = build_can_use_tool(never, cwd=wt, write_boundary=[wt, item_dir])

    async def shell(cmd):
        return await can_sh("Bash", {"command": cmd}, None)

    for cmd in ("pytest -q", "python -m pytest tests/ -v", "git add -A && git commit -m 'x'",
                "npm install", "mkdir -p src/pkg", f"pytest {wt}/tests"):
        r = asyncio.run(shell(cmd))
        ok(f"shell auto-allows in-boundary: {cmd[:28]}", type(r).__name__ == "PermissionResultAllow")

    # An absolute path outside the boundary is the accident this catches. It must NOT auto-allow —
    # it escalates (here `never` raises, proving we reached the prompt rather than silently allowing).
    for cmd in (f"rm -rf {repo}", "cat /etc/passwd > /tmp/leak.txt", "rm -rf /"):
        try:
            asyncio.run(shell(cmd))
            ok(f"out-of-boundary shell escalates: {cmd[:28]}", False, "auto-allowed!")
        except AssertionError as e:
            ok(f"out-of-boundary shell escalates: {cmd[:28]}", "escalated to approval" in str(e))

    # No boundary (a plain chat session) → unchanged: a mutating command still escalates.
    can_plain = build_can_use_tool(never, cwd=wt)
    try:
        asyncio.run(can_plain("Bash", {"command": "pytest -q"}, None))
        ok("no boundary → mutating shell still escalates", False, "auto-allowed!")
    except AssertionError as e:
        ok("no boundary → mutating shell still escalates", "escalated to approval" in str(e))

    # cwd OUTSIDE the boundary → the boundary grants nothing (the worktree is the whole guarantee).
    can_out = build_can_use_tool(never, cwd=repo, write_boundary=[wt, item_dir])
    try:
        asyncio.run(can_out("Bash", {"command": "pytest -q"}, None))
        ok("cwd outside boundary → shell escalates", False, "auto-allowed!")
    except AssertionError as e:
        ok("cwd outside boundary → shell escalates", "escalated to approval" in str(e))

    # Read-only shell is allowed everywhere (same access as Read/Grep) — including the `2>&1`
    # carve-out (F7), which agents reach for constantly on ordinary reads.
    r = asyncio.run(can_plain("Bash", {"command": "grep -rn foo . 2>&1"}, None))
    ok("read-only shell with 2>&1 auto-allows", type(r).__name__ == "PermissionResultAllow")

    G.remove_worktree(repo, RID, "ggg777")


def test_locks_and_guards(repo: Path) -> None:
    print("op lock + state guards")
    lock = G._lock_for(repo)
    lock.acquire()
    try:
        G.merge_to_main(repo, RID, "zzz", "item/zzz")
        ok("busy repo raises GitBusy", False)
    except G.GitBusy:
        ok("busy repo raises GitBusy", True)
    finally:
        lock.release()
    ok("non-repo health fails soft", G.worktree_health(Path("/tmp"), RID, "x")["ok"] is False)
    try:
        G.create_worktree(Path("/tmp"), RID, "x", "t")
        ok("create on non-repo refused", False)
    except G.GitError:
        ok("create on non-repo refused", True)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        # Worktrees live under a SuperMe-owned home (~/.superme/worktrees/<repo-id>/); point it
        # at the throwaway dir so a suite run never touches the real one. Read per call, so
        # setting it here (after import) is enough.
        os.environ["SUPERME_WORKTREES_HOME"] = str(tmp / "worktrees-home")
        repo = make_repo(tmp)
        test_lifecycle(repo)
        test_merge_flow(repo)
        test_conflict_resolution(repo)
        test_family(repo)
        test_freeze_boundary(repo, tmp)
        test_locks_and_guards(repo)
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
