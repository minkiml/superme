"""Review reads the item's branch, not whatever the repo root has checked out.

The repo root can never have the item's branch checked out — git forbids one branch in two
worktrees — so `base...HEAD` at the root describes a different tree. Every test here pins that
`read_item_diff` answers from the worktree, and the CONTROL pins that the shell form it replaces
really does answer wrong.

Run: PYTHONPATH=. python scripts/test_item_diff.py
"""

import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superme_agent.core import git_layer as gl                # noqa: E402
from superme_agent.core.permissions import (                  # noqa: E402
    WRONG_TREE_NUDGE, build_can_use_tool, reads_ambient_head,
)
from superme_agent.harness.tools.dev_tools.items import _read_item_diff   # noqa: E402

PASS = 0


def ok(label: str, cond, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {label} {detail}"
    PASS += 1
    print(f"  ok - {label}")


def git(cwd, *args) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def build_fixture(root: Path) -> tuple[Path, Path, Path]:
    """A repo on `main`, an item branch in its own worktree, and the item folder naming both."""
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t.t")
    git(repo, "config", "user.name", "t")
    (repo / "kept.py").write_text("a = 1\n", encoding="utf-8")
    (repo / "other.py").write_text("b = 2\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")

    wt = root / "wt"
    git(repo, "worktree", "add", "-b", "item/x", str(wt))
    (wt / "kept.py").write_text("a = 1\nc = 3\n", encoding="utf-8")
    (wt / "new.py").write_text("d = 4\n", encoding="utf-8")
    git(wt, "add", "-A")
    git(wt, "commit", "-m", "the item's work")

    dev = root / "dev"
    item_dir = dev / "work-items" / "abc123abc123"
    item_dir.mkdir(parents=True)
    (item_dir / "item.md").write_text(
        f"---\nid: abc123abc123\ntitle: t\nkind: implementation\nphase: review\n"
        f"git_worktree: {wt}\ngit_base: main\n---\n\nbody\n", encoding="utf-8")
    return repo, wt, dev


def call(dev: Path, item_id: str = "abc123abc123", **args) -> tuple[bool, str]:
    h = _read_item_diff(store=None, context_id="t", dev_root=dev, bound_item_id=item_id)
    r = asyncio.run(h({"item_id": item_id, **args}))
    return bool(r.get("is_error")), r["content"][0]["text"]


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        repo, wt, dev = build_fixture(Path(td))

        # --- THE CONTROL. Without this, the tests below prove nothing: they would pass just as
        # well if the shell form had been fine all along.
        ok("control: `main...HEAD` at the repo root reports NO changes",
           git(repo, "diff", "--stat", "main...HEAD") == "")
        ok("control: the repo root is not on the item's branch",
           git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main")
        ok("control: the same command IN the worktree does report them",
           "new.py" in git(wt, "diff", "--stat", "main...HEAD"))

        err, out = call(dev)
        ok("the tool reports the branch's files where the root reported none", not err)
        ok("both changed files are named", "kept.py" in out and "new.py" in out)
        ok("`other.py` is untouched and absent", "other.py" not in out)
        ok("the branch is named, not the root's", "item/x" in out)
        ok("the commit is listed", "the item's work" in out)
        ok("the counts are the branch's", "2 file(s), +2 −0" in out)
        ok("the patch rides along", "```diff" in out)

        err, out = call(dev, path="new.py")
        ok("`path` narrows to one file", not err and "1 file(s)" in out and "kept.py" not in out)

        err, out = call(dev, path="other.py")
        ok("a path with no branch changes says so rather than returning a bare header",
           not err and "No changes under" in out)
        ok("...and still shows the branch HAS commits, so 'empty' is not read as 'nothing built'",
           "1 commit(s)" in out)

        # Uncommitted work is invisible to `base...HEAD` and would not merge — say so.
        (wt / "dirty.py").write_text("e = 5\n", encoding="utf-8")
        err, out = call(dev)
        ok("uncommitted worktree files are reported separately",
           "dirty.py" in out and "will not merge" in out)

        # --- refusals
        (dev / "work-items" / "abc123abc123" / "item.md").write_text(
            "---\nid: abc123abc123\nphase: review\ngit_base: main\n---\n", encoding="utf-8")
        err, out = call(dev)
        ok("no worktree refuses instead of falling back to the repo root", err)
        ok("...and does not name a tree it did not read", "main...HEAD" not in out)

        err, out = call(dev, item_id="nope")
        ok("an unbound item id is refused", err)

    permission_guard()
    print(f"\nALL GREEN — {PASS} checks passed.")


def decide(fn, tool: str, args: dict):
    class Ctx:  # the SDK hands one in; nothing here reads it
        pass
    return asyncio.run(fn(tool, args, Ctx()))


def permission_guard() -> None:
    """The shell path. The tool is only the better route until the wrong one stops answering."""
    # Every wrong form is a REAL command pulled from August review runs.
    wrong = ["git diff --stat main...HEAD",
             "git log main..HEAD --oneline",
             "git diff main...HEAD -- ledger/commands.py",
             "git status --short && git diff --stat main...HEAD",
             "git diff --stat test/e2e-hub...HEAD",
             "pwd && git diff --stat main...HEAD && echo --- && git log main..HEAD"]
    right = ["git -C /tmp/wt diff --stat main...HEAD",
             "git --git-dir=/tmp/wt/.git diff main...HEAD",
             "git diff --stat main...item/abc-thing",
             "git log --oneline -5",
             "git status --porcelain",
             "git branch -a"]
    ok("every wrong form from the traces is detected", all(map(reads_ambient_head, wrong)))
    ok("no correct form is", not any(map(reads_ambient_head, right)))
    # A path is not a revision, and a non-git command is not ours to judge.
    ok("`HEAD` inside a filename is not a revision",
       not reads_ambient_head("git log --oneline -3 src/HEADER.py"))
    ok("a non-git command naming HEAD is untouched", not reads_ambient_head("echo HEAD"))

    async def approve(_tool, _args):
        return True

    fn = build_can_use_tool(approve, wrong_tree_nudge=WRONG_TREE_NUDGE)
    r = decide(fn, "Bash", {"command": "git diff --stat main...HEAD"})
    ok("review REFUSES the wrong form, though it is read-only",
       type(r).__name__ == "PermissionResultDeny" and "read_item_diff" in r.message)
    r = decide(fn, "Bash", {"command": "git -C /tmp/wt diff --stat main...HEAD"})
    ok("...and allows the redirected form", type(r).__name__ == "PermissionResultAllow")
    r = decide(fn, "Bash", {"command": "git log --oneline -5"})
    ok("...and does not touch a genuine question about the repo root",
       type(r).__name__ == "PermissionResultAllow")

    # CONTROL: every other phase is unchanged. Without the flag the same command sails through,
    # which is what made this invisible for a month.
    fn2 = build_can_use_tool(approve)
    r = decide(fn2, "Bash", {"command": "git diff --stat main...HEAD"})
    ok("control: without the flag the wrong form is still allowed (build/vet unchanged)",
       type(r).__name__ == "PermissionResultAllow")


main()
