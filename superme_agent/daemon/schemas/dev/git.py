"""Response schemas for the work-item git routes (routers/dev/git.py — workspace-workflow S4)."""

from __future__ import annotations

from pydantic import BaseModel


class GitHealthResponse(BaseModel):
    """One item's live git state — health check + FE decoration (derived, never stored)."""
    ok: bool
    reason: str | None = None
    branch: str | None = None
    worktree: str | None = None
    trunk: str | None = None    # the repo's ANCHOR branch — what ahead/behind and the merge target are
    # The repo's review mode (`fast` | `strict`), read live and echoed here so the rule governing the
    # merge is visible where the merge is. Not stored on the item: a mode flip applies immediately.
    review_mode: str | None = None
    branch_exists: bool | None = None
    dir_exists: bool | None = None
    registered: bool | None = None
    dirty: list[str] | None = None
    merged: bool | None = None
    ahead: int | None = None    # branch commits not yet on trunk
    behind: int | None = None   # trunk commits not yet in the branch (freshness debt)


class GitMergeResponse(BaseModel):
    """The review-gate merge. `path` says which route executed: `main` (heavy, with backup ref)
    or `parent` (blocking child's light merge into its parent's branch)."""
    ok: bool
    merged: bool
    path: str  # main | parent
    already_merged: bool | None = None
    merge_commit: str | None = None
    backup_ref: str | None = None
    target: str | None = None
    conflicts: list[str] | None = None
    stash_warning: str | None = None
    # The merge act owns freshness (§2.3). Neither value is a failure — the anchor moved while the
    # item sat at the gate. `park` = syncing it in conflicts, the owner resolves. `revet` = it
    # merged cleanly but over `stale_paths` this item also changed, so the evidence is stale and
    # the item takes one vet cycle before it can be approved again.
    freshness: str | None = None       # park | revet (absent when the anchor was already current)
    stale_paths: list[str] | None = None
    # Standing freshness-lint warnings raised by this merge. (The knowledge WRITE moved to close —
    # renovation §2.3 — so a merge no longer reports ops applied or folded.)
    lint_warnings: list[str] | None = None


class GitRevertResponse(BaseModel):
    ok: bool
    reverted: bool
    target: str | None = None
    head: str | None = None


class PrCommit(BaseModel):
    """One commit as the walkthrough shows it. `body` has the SuperMe trailer block stripped —
    that block is what put the commit in this group, so repeating it inside would be noise."""
    sha: str
    short: str
    subject: str
    body: str | None = None


class PrFile(BaseModel):
    """One file inside a task group, with the churn that group put on it (summed over the group's
    commits, which is what ranks it — the biggest change is where the risk is)."""
    path: str
    plus: int
    minus: int


class PrGroup(BaseModel):
    """One task's slice of the branch. `task` is null for commits that carry no `SuperMe-Task`
    trailer — they are shown last rather than hidden."""
    task: str | None = None
    title: str | None = None    # the plan's `## Tasks` line for this id, when the plan still has it
    done: bool | None = None
    commits: list[PrCommit]
    files: list[PrFile]


class PrStat(BaseModel):
    """Header numbers: commit COUNT over the fork point, and the NET diff that actually lands
    (not the sum of per-commit churn — a line written in t1 and rewritten in t3 lands once)."""
    commits: int
    files: int
    insertions: int
    deletions: int


class PrViewResponse(BaseModel):
    """The dedicated PR page (§4.4): the review report on the left, the task-grouped diff
    walkthrough on the right. Entirely derived at read time — nothing here is stored."""
    ok: bool
    id: str
    title: str
    phase: str | None = None
    branch: str
    base: str | None = None
    target: str | None = None
    review_mode: str | None = None
    pr_open: bool
    merged: bool
    merge_commit: str | None = None
    report: str | None = None   # reports/report-review.md, rendered by the FE
    stat: PrStat
    groups: list[PrGroup]


class PrPatch(BaseModel):
    sha: str
    short: str
    subject: str
    patch: str
    truncated: bool | None = None


class PrDiffResponse(BaseModel):
    """One file's patches under one task — fetched when the reader opens the row, never up front."""
    ok: bool
    id: str
    path: str
    task: str | None = None
    patches: list[PrPatch]


class GitResolveResponse(BaseModel):
    """Resolve-with-Agent accepted: the conflicted sync was left in the item's worktree and a
    background resolution run is in flight (poll GET /dev for run state)."""
    ok: bool
    status: str
    id: str
    conflicts: list[str] | None = None
