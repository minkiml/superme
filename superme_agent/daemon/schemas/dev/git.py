"""Response schemas for the work-item git routes (routers/dev/git.py — workspace-workflow S4)."""

from __future__ import annotations

from pydantic import BaseModel


class GitHealthResponse(BaseModel):
    """One item's live git state — health check + FE decoration (derived, never stored)."""
    ok: bool
    reason: str | None = None
    branch: str | None = None
    worktree: str | None = None
    trunk: str | None = None
    branch_exists: bool | None = None
    dir_exists: bool | None = None
    registered: bool | None = None
    dirty: list[str] | None = None
    merged: bool | None = None
    ahead: int | None = None    # branch commits not yet on trunk
    behind: int | None = None   # trunk commits not yet in the branch (freshness debt)


class GitSyncResponse(BaseModel):
    """Freshness merge (trunk INTO the item branch). Conflicts: `in_tree=False` means the merge
    was aborted and reported; True means it was deliberately left in the tree for resolution."""
    ok: bool
    merged: bool
    up_to_date: bool | None = None
    commit: str | None = None
    conflicts: list[str] | None = None
    in_tree: bool | None = None


class GitMergeResponse(BaseModel):
    """The deliver-gate merge. `path` says which route executed: `main` (heavy, with backup ref)
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
    # D7 knowledge pipeline (S6): ops applied atomically with a main merge · the parent a blocking
    # child's delta folded into · standing freshness-lint warnings raised by this merge.
    knowledge_ops_applied: int | None = None
    knowledge_folded_into: str | None = None
    lint_warnings: list[str] | None = None


class GitRevertResponse(BaseModel):
    ok: bool
    reverted: bool
    target: str | None = None
    head: str | None = None


class GitResolveResponse(BaseModel):
    """Resolve-with-Agent accepted: the conflicted sync was left in the item's worktree and a
    headless resolution run is in flight (poll GET /dev for run state)."""
    ok: bool
    status: str
    id: str
    conflicts: list[str] | None = None
