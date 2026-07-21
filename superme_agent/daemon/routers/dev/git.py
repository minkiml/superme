"""Work-item git routes (workspace-workflow S4/D4): health check, freshness sync, the review
merge (heavy main path / light blocking-child path), backup-ref revert, and Resolve-with-Agent.

These are the OWNER's git surface — mechanics only; the readiness brief that fronts the review
decision lands at S6. The mechanics themselves live in core/git_layer; item records in
core/dev_knowledge. Every route re-reads the item (authoritative state, never a stored flag).
"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...app_state import DevKnowledgeService, DevStore, SystemSpine, get_dev, get_dev_store, get_spine
from ....core import git_layer
from ....gateway import contexts
from ...services.runs import _begin_run, _run_background_resolve
from ...schemas.dev.git import (
    GitHealthResponse, GitSyncResponse, GitMergeResponse, GitRevertResponse, GitResolveResponse,
)

log = logging.getLogger("superme-agent")

router = APIRouter()


class GitBody(BaseModel):
    context_id: str = "global"


def _load(context_id: str, item_id: str, dev: DevKnowledgeService):
    """Resolve (ctx, dev_root, item) or raise the shared 4xx set."""
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise HTTPException(status_code=400, detail="context has no internal root")
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    return ctx, dev_root, item


def _require_worktree(item: dict) -> str:
    wt = item.get("git_worktree")
    if not wt:
        raise HTTPException(status_code=409, detail="item has no worktree (created on build entry)")
    return str(wt)


@router.get("/dev/work-items/{item_id}/git", response_model=GitHealthResponse,
            response_model_exclude_unset=True)
async def dev_work_item_git(item_id: str, context_id: str = "global",
                            dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """The item's live git state (derived at read time, never stored): branch/dir/registration
    existence, dirty files, ahead/behind vs trunk (behind = freshness debt), merged-into-trunk."""
    ctx, _root, item = _load(context_id, item_id, dev)
    if not item.get("git_branch") and not item.get("git_worktree"):
        return {"ok": False, "reason": "no git record (worktree is created on build entry)"}
    return git_layer.worktree_health(ctx.cwd, ctx.id, item_id, item.get("git_branch"))


class SyncBody(GitBody):
    # True leaves a conflicted merge IN the tree (the resolve route does this itself; direct use
    # is for a human who wants to inspect). Default aborts + reports — the safe path.
    leave_conflicts: bool = False


@router.post("/dev/work-items/{item_id}/git/sync", response_model=GitSyncResponse,
             response_model_exclude_unset=True)
async def dev_work_item_git_sync(item_id: str, body: SyncBody,
                                 dev: DevKnowledgeService = Depends(get_dev),
                                 dev_store: DevStore = Depends(get_dev_store),
                                 spine: SystemSpine = Depends(get_spine)) -> dict:
    """Freshness merge (D4): merge the trunk INTO the item branch, inside its worktree — run
    during long builds and always before the review merge, so main-merge is trivial."""
    ctx, _root, item = _load(body.context_id, item_id, dev)
    wt = _require_worktree(item)
    if spine.is_item_running(body.context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    try:
        res = git_layer.sync_from_main(ctx.cwd, wt, leave_conflicts=body.leave_conflicts)
    except (git_layer.GitError, git_layer.GitBusy) as e:
        raise HTTPException(status_code=409, detail=str(e))
    dev_store.log_event(body.context_id, "git.sync",
                        ("Synced trunk into item branch" if res.get("merged")
                         else "Already up to date" if res.get("up_to_date")
                         else f"Sync hit {len(res.get('conflicts') or [])} conflict(s)"),
                        item_id=item_id, actor="owner", meta=res)
    return {"ok": True, "merged": bool(res.get("merged")), **{k: v for k, v in res.items()
                                                              if k != "merged"}}


@router.post("/dev/work-items/{item_id}/git/merge", response_model=GitMergeResponse,
             response_model_exclude_unset=True)
async def dev_work_item_git_merge(item_id: str, body: GitBody,
                                  dev: DevKnowledgeService = Depends(get_dev),
                                  dev_store: DevStore = Depends(get_dev_store),
                                  spine: SystemSpine = Depends(get_spine)) -> dict:
    """The review-gate merge (owner-fired), as a raw route. Thin wrapper over
    `services.git_ops.review_merge` — the SAME body `advance_item` runs when the owner (or deputy)
    approves at review, so 'the review decision IS the merge' holds whether it's fired here or by
    the Approve transition (B2). Routes by topology (D4): a BLOCKING child merges into its parent's
    branch (light path), everything else to the trunk (heavy path — overlap refusal, backup ref,
    never-merge-twice). Conflicts on the main path → 200 with the conflict list (sync + resolve,
    then retry / approve again)."""
    from ...services import git_ops
    ctx, _root, _item = _load(body.context_id, item_id, dev)
    return git_ops.review_merge(ctx, body.context_id, item_id,
                                dev=dev, dev_store=dev_store, spine=spine)


@router.post("/dev/work-items/{item_id}/git/revert", response_model=GitRevertResponse)
async def dev_work_item_git_revert(item_id: str, body: GitBody,
                                   dev: DevKnowledgeService = Depends(get_dev),
                                   dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Restore the trunk to its pre-merge state via the item's recorded backup ref — the
    always-offered undo behind every main merge (D4 guardrail). Safe-only: refuses once anything
    else has landed on top. Clears the item's merge record (the branch itself is untouched)."""
    ctx, dev_root, item = _load(body.context_id, item_id, dev)
    backup = item.get("git_backup_ref")
    if not backup:
        raise HTTPException(status_code=409, detail="item has no recorded backup ref (no main merge to revert)")
    try:
        res = git_layer.revert_merge(ctx.cwd, backup)
    except (git_layer.GitError, git_layer.GitBusy) as e:
        raise HTTPException(status_code=409, detail=str(e))
    dev.set_work_item_git(dev_root, item_id, git_merge_commit=None, git_merged_at=None,
                          git_backup_ref=None)
    dev_store.log_event(body.context_id, "git.revert",
                        f"Reverted trunk merge (restored {res['head'][:10]})",
                        item_id=item_id, actor="owner", meta=res)
    return {"ok": True, **res}


@router.post("/dev/work-items/{item_id}/git/resolve", response_model=GitResolveResponse)
async def dev_work_item_git_resolve(item_id: str, body: GitBody,
                                    dev: DevKnowledgeService = Depends(get_dev),
                                    spine: SystemSpine = Depends(get_spine)) -> dict:
    """Resolve-with-Agent (D4): the human decides WHETHER, the agent resolves — they never
    hand-edit conflict markers. Re-runs the freshness sync leaving conflicts IN the worktree,
    then fires a background resolution run (write-sandboxed to the worktree); the daemon completes
    the merge mechanically and the item re-enters `vet`. 409 if the sync is clean (nothing
    to resolve) or a run is in flight."""
    ctx, _root, item = _load(body.context_id, item_id, dev)
    wt = Path(_require_worktree(item))
    if spine.is_item_running(body.context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    try:
        res = git_layer.sync_from_main(ctx.cwd, wt, leave_conflicts=True)
    except (git_layer.GitError, git_layer.GitBusy) as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not res.get("conflicts"):
        raise HTTPException(status_code=409,
                            detail="nothing to resolve — the sync merged cleanly (or was up to date)")
    model = spine.effective_model(body.context_id, item_model=item.get("model"))
    effort = spine.effective_effort(body.context_id, item_effort=item.get("effort"))
    if not _begin_run(ctx, body.context_id, item_id, "resolve", model, phase=item.get("phase")):
        # Undo the in-tree conflict state before refusing — never leave a mess behind a 409.
        git_layer._git(wt, "merge", "--abort", check=False)
        raise HTTPException(status_code=409, detail="a run is already in progress for this item")
    asyncio.create_task(_run_background_resolve(
        ctx, body.context_id, item_id, wt, res["conflicts"], model, effort))
    return {"ok": True, "status": "resolving", "id": item_id, "conflicts": res["conflicts"]}
