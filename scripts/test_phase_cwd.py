"""Which tree each phase stands in.

Everything that reads or writes the item's code now stands in its tree.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sources import src
from superme_agent.core.vocab import kind_profiles

PASS = 0


def ok(label: str, cond, detail: str = "") -> None:
    global PASS
    assert cond, f"FAILED: {label} {detail}"
    PASS += 1
    print(f"  ok  {label}")


def test_the_phases_that_touch_code_share_one_tree() -> None:
    print("which phases stand in the item's worktree")
    for phase in ("plan", "build", "vet", "review"):
        ok(f"{phase} runs in the worktree", kind_profiles.phase_uses_worktree(phase))
    # triage precedes any code, and close runs after the merge with nothing left to read.
    for phase in ("triage", "close"):
        ok(f"{phase} stays at the repo root", not kind_profiles.phase_uses_worktree(phase))


def test_the_tree_exists_before_the_first_phase_that_needs_it() -> None:
    print("\nwhen the worktree is created")
    gates = src("superme_agent/daemon/services/gates.py")
    ok("creation is keyed on entering plan, not build",
       re.search(r'nxt in \("plan", "build"\)[^\n]*profile\.worktree', gates))
    ok("...and is still skipped when one already exists", "not item.get(\"git_worktree\")" in gates)
    # A kind with no worktree in its profile must never get one.
    ok("...and only for a kind whose profile declares one", "profile.worktree" in gates)


def test_a_phase_run_stands_where_its_phase_says() -> None:
    print("\nthe runner puts the turn there")
    bg = src("superme_agent/daemon/services/runs/background.py")
    ok("the intake runner swaps cwd to the item's worktree", "item_worktree_cwd" in bg)
    ok("...and the shell may name the tree it is standing in", "shell_roots=" in bg)
    ops = src("superme_agent/daemon/services/git_ops.py")
    ok("the swap is one function, beside the research one", "def item_worktree_cwd" in ops)
    ok("...and returns the repo root when there is no worktree yet",
       "return ctx.cwd" in ops.split("def item_worktree_cwd")[1][:600])


def test_the_write_boundary_did_not_widen() -> None:
    print("\nplan and review still may not write code")
    bg = src("superme_agent/daemon/services/runs/background.py")
    body = bg.split("turn_kwargs = dict(")[1][:1200]
    ok("file writes stay inside the item folder", "write_boundary=[item_dir]" in body)
    ok("...and the kernel sandbox is not handed the worktree to write",
       "sandbox_writes=[item_dir" in body)


def test_the_branch_dies_with_its_worktree() -> None:
    """A squash already put the item's content on the anchor, so the branch holds nothing."""
    print("\nbranch cleanup")
    import subprocess, tempfile
    from superme_agent.core import git_layer
    root = Path(tempfile.mkdtemp(prefix="branchlife-"))
    repo = root / "repo"
    repo.mkdir()

    def git(*a, cwd=repo):
        return subprocess.run(["git", *a], cwd=str(cwd), capture_output=True, text=True,
                              encoding="utf-8")

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t"); git("config", "user.name", "t")
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    git("add", "-A"); git("commit", "-qm", "base")

    rec = git_layer.create_worktree(repo, "probe", "item-a", base="main")
    branch = rec["branch"]
    ok("the item has a branch", branch in git("branch", "--list", branch).stdout)
    ok("...and a worktree holding it", Path(rec["worktree"]).is_dir())

    # Terminal cleanup, the way clearance calls it.
    res = git_layer.remove_worktree(repo, "probe", "item-a", branch=branch)
    ok("the worktree dir is gone", res["verified"] and not Path(rec["worktree"]).is_dir())
    ok("...and the branch went with it", res["branch_deleted"] is True)
    ok("...verified against git itself", branch not in git("branch", "--list", branch).stdout)

    # The re-run path removes the dir and re-cuts, so it must keep the branch.
    rec2 = git_layer.create_worktree(repo, "probe", "item-b", base="main")
    kept = git_layer.remove_worktree(repo, "probe", "item-b")
    ok("a removal that names no branch leaves it standing",
       kept["branch_deleted"] is None
       and rec2["branch"] in git("branch", "--list", rec2["branch"]).stdout)
    import shutil; shutil.rmtree(root, ignore_errors=True)


def main() -> None:
    test_the_phases_that_touch_code_share_one_tree()
    test_the_tree_exists_before_the_first_phase_that_needs_it()
    test_a_phase_run_stands_where_its_phase_says()
    test_the_write_boundary_did_not_widen()
    test_the_branch_dies_with_its_worktree()
    print(f"\nALL GREEN — {PASS} checks passed.")


if __name__ == "__main__":
    main()
