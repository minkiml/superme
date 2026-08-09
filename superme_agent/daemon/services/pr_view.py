"""The PR page's read model (renovation §4.4) — the branch, assembled for a human to review.

This is the surface `strict` exists for: the deputy has approved, the diff has not landed, and the
owner reads it here before it does. Everything on the page is DERIVED at read time from the branch
and the item's own artifacts — nothing is stored, so the page can never show a stale rendering of a
branch that has since moved.

**Why grouped by task.** SuperMe's build commits carry a `SuperMe-Task` trailer by construction
(4c-bis), so the walkthrough can be organized by the plan's tasks rather than by file — each group
traceable to the task it implements and to the cycle report that verified it. No forge can do that
with an ordinary branch, and it is exactly what pre-squash tidying would have thrown away.

The page is read-only. Its one action (Merge) is the ordinary review advance, fired from the FE —
this module never merges, never writes, never touches the tree.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException

from ...core import artifacts, git_layer
from . import git_ops

log = logging.getLogger("superme-agent")

# Commits with no `SuperMe-Task` trailer (checkpoints outside a task, a conflict resolution, a
# pre-trailer item) still have to appear — a walkthrough that hides commits is worse than one with
# an untidy group. They collect here, last.
UNLABELLED = "unlabelled"


def _read(path: Path) -> str:
    try:
        return path.read_text() if path.is_file() else ""
    except OSError:
        log.warning("pr view: could not read %s", path)
        return ""


def _load(ctx, item_id: str, dev) -> tuple[dict, Path]:
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    if not item.get("git_branch"):
        raise HTTPException(status_code=409,
                            detail="item has no branch (created on build entry) — nothing to review")
    return item, dev_root / "work-items" / item_id


def task_of(commit: dict) -> str:
    """Which task a commit belongs to, from its trailer. A checkpoint commit is tagged
    `SuperMe-Task: t3 (wip)` (build/references/commit-style.md), so only the first token is the id
    — a checkpoint belongs to its task's group, not to a group of its own."""
    raw = (commit.get("trailers") or {}).get("SuperMe-Task") or ""
    return raw.split()[0] if raw.split() else UNLABELLED


def _group_commits(commits: list[dict], tasks: list[dict]) -> list[dict]:
    """Commits → task groups, in PLAN order (not commit order): the owner reads the branch against
    the plan they approved, so a task built out of order still reads where they expect it. Tasks
    with no commits are dropped — the walkthrough shows what happened, and an unbuilt task is the
    cycle reports' story, not the diff's."""
    titles = {t["id"]: t for t in tasks}
    buckets: dict[str, list[dict]] = {}
    for c in commits:
        buckets.setdefault(task_of(c), []).append(c)
    # Plan order first, then any task id the plan doesn't know (a renumbered plan, a hand commit),
    # then the unlabelled bucket last.
    order = [t["id"] for t in tasks if t["id"] in buckets]
    order += [k for k in buckets if k not in order and k != UNLABELLED]
    if UNLABELLED in buckets:
        order.append(UNLABELLED)
    groups = []
    for key in order:
        group_commits = buckets[key]
        churn: dict[str, dict] = {}
        for c in group_commits:
            for f in c["files"]:
                row = churn.setdefault(f["path"], {"path": f["path"], "plus": 0, "minus": 0})
                row["plus"] += f["plus"]
                row["minus"] += f["minus"]
        task = titles.get(key)
        groups.append({
            "task": None if key == UNLABELLED else key,
            "title": (task or {}).get("text") or None,
            "done": (task or {}).get("done"),
            "commits": [{"sha": c["sha"], "short": c["short"], "subject": c["subject"],
                         "body": _body_without_trailers(c)} for c in group_commits],
            # Churn-ranked — the biggest change in the group is where the risk is, and it is the
            # one a reader who stops after two files should have seen.
            "files": sorted(churn.values(), key=lambda f: -(f["plus"] + f["minus"])),
        })
    return groups


def _body_without_trailers(commit: dict) -> str:
    """The commit body as the project reads it. The trailer block is SuperMe's own routing data —
    it is what BUILT this group, so repeating it inside the group is noise."""
    body = commit.get("body") or ""
    if not commit.get("trailers"):
        return body.strip()
    keep = []
    for line in body.rstrip().splitlines():
        if line.strip() and all(not line.strip().startswith(f"{k}:") for k in commit["trailers"]):
            keep.append(line)
        elif not line.strip():
            keep.append(line)
    return "\n".join(keep).strip()


def _base_of(item: dict, ctx, spine) -> str:
    """Where this branch forked. The RECORDED base, because that is the truth of where the work
    began — a blocking child forked from its parent's branch, and the repo's anchor may have been
    re-pointed since. Falls back to the anchor for records written before `git_base` existed."""
    return str(item.get("git_base") or "") or (git_ops.repo_anchor(ctx, spine) or "")


def pr_view(ctx, context_id: str, item_id: str, *, dev, spine) -> dict:
    """The whole page in one read: the review report on the left, the task-grouped walkthrough on
    the right, and the header facts that say whether the merge is still available."""
    item, item_dir = _load(ctx, item_id, dev)
    branch = str(item["git_branch"])
    base = _base_of(item, ctx, spine)
    target = git_layer.resolve_anchor(ctx.cwd, git_ops.repo_anchor(ctx, spine))
    commits = git_layer.branch_commits(ctx.cwd, branch, base)
    tasks = artifacts.parse_tasks(_read(item_dir / "artifacts" / "plan.md"))
    stat = git_layer.branch_stat(ctx.cwd, branch, base)
    # The review notes, joined onto their own task's group. Derived at read time like everything
    # else here: the requirement from the plan's checks, the pointer and the deviation from the
    # cycle that built it, the verdicts from the ledger.
    guide = artifacts.pr_task_guide(item_dir)
    groups = [{**g, **{k: v for k, v in (guide.get(g["task"]) or {}).items() if k != "cycle"}}
              for g in _group_commits(commits, tasks)]
    return {
        "ok": True,
        "id": item_id,
        "title": item.get("title") or item_id,
        "phase": item.get("phase"),
        "branch": branch,
        "base": base,
        "target": target,
        "review_mode": git_ops.repo_review_mode(ctx, spine),
        "pr_open": git_ops.pr_open(item),
        "merged": bool(item.get("git_merge_commit")),
        "merge_commit": item.get("git_merge_commit"),
        "report": _read(item_dir / "reports" / "report-review.md") or None,
        "stat": {"commits": len(commits), "files": stat["files"],
                 "insertions": stat["insertions"], "deletions": stat["deletions"]},
        "groups": groups,
    }


def pr_file_diff(ctx, context_id: str, item_id: str, *, path: str, task: str | None,
                 dev, spine) -> dict:
    """One file's patches inside one task group — fetched when the reader opens the row, because
    a branch's full diff is the one thing a review page must not make them wait for.

    The commit shas are resolved HERE from the branch rather than accepted from the caller: the
    page asks for 'this file, under this task', and the server decides which commits that is."""
    item, _dir = _load(ctx, item_id, dev)
    branch = str(item["git_branch"])
    commits = git_layer.branch_commits(ctx.cwd, branch, _base_of(item, ctx, spine))
    want = task or UNLABELLED
    picked = [c for c in commits
              if task_of(c) == want and any(f["path"] == path for f in c["files"])]
    subjects = {c["sha"]: c["subject"] for c in picked}
    patches = git_layer.commit_patches(ctx.cwd, [c["sha"] for c in picked], path)
    return {"ok": True, "id": item_id, "path": path, "task": task,
            "patches": [{**p, "short": p["sha"][:10], "subject": subjects.get(p["sha"], "")}
                        for p in patches]}
