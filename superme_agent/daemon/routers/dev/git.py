"""Work-item git routes: health check, freshness sync, the review merge, backup-ref revert, and
resolve-with-agent.

The mechanics live in `core/git_layer`. Every route re-reads the item, never a stored flag.
"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ...app_state import DevKnowledgeService, DevStore, SystemSpine, get_dev, get_dev_store, get_spine
from ....core import git_layer
from ....gateway import contexts
from ...services import git_ops, pr_view
from ...services.runs import begin_run, run_background_resolve
from ...schemas.dev.git import (GitBody, GitHealthResponse, GitMergeResponse, GitResolveResponse,
                                GitRevertResponse, PrDiffResponse, PrViewResponse)

log = logging.getLogger("superme-agent")

router = APIRouter()


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
                            dev: DevKnowledgeService = Depends(get_dev),
                            spine: SystemSpine = Depends(get_spine)) -> dict:
    """The item's live git state, derived at read time: branch and dir existence, dirty files, ahead and
    behind versus the anchor, merged.

    Also echoes the repo's `review_mode`, read live, so a mode flip applies to items already at
    review."""
    ctx, _root, item = _load(context_id, item_id, dev)
    mode = {"review_mode": git_ops.repo_review_mode(ctx, spine)}
    if not item.get("git_branch") and not item.get("git_worktree"):
        return {"ok": False, "reason": "no git record (worktree is created on build entry)", **mode}
    return {**git_layer.worktree_health(ctx.cwd, ctx.id, item_id, item.get("git_branch"),
                                        trunk=git_ops.repo_anchor(ctx, spine),
                                        merge_commit=item.get("git_merge_commit")), **mode}


@router.post("/dev/work-items/{item_id}/git/merge", response_model=GitMergeResponse,
             response_model_exclude_unset=True)
async def dev_work_item_git_merge(item_id: str, body: GitBody,
                                  dev: DevKnowledgeService = Depends(get_dev),
                                  dev_store: DevStore = Depends(get_dev_store),
                                  spine: SystemSpine = Depends(get_spine)) -> dict:
    """The review-gate merge as a raw route — a thin wrapper over the SAME body `advance_item` runs.

    Routes by topology: a blocking child merges into its parent's branch, everything else to the
    trunk."""
    ctx, _root, _item = _load(body.context_id, item_id, dev)
    return git_ops.review_merge(ctx, body.context_id, item_id,
                                dev=dev, dev_store=dev_store, spine=spine)


@router.get("/dev/work-items/{item_id}/pr", response_model=PrViewResponse)
async def dev_work_item_pr(item_id: str, context_id: str = "global",
                           dev: DevKnowledgeService = Depends(get_dev),
                           spine: SystemSpine = Depends(get_spine)) -> dict:
    """The dedicated PR page — `strict`'s review surface, and readable in any mode. Read-only
    by construction: the page's one action is the ordinary review Approve, which merges."""
    ctx = contexts.resolve(context_id, "dev")
    return pr_view.pr_view(ctx, context_id, item_id, dev=dev, spine=spine)


@router.get("/dev/work-items/{item_id}/pr/diff", response_model=PrDiffResponse)
async def dev_work_item_pr_diff(item_id: str, path: str, task: str | None = None,
                                context_id: str = "global",
                                dev: DevKnowledgeService = Depends(get_dev),
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """One file's patches under one task group, fetched when the reader expands the row — a
    branch's whole diff is the one thing a review page must not make them wait for."""
    ctx = contexts.resolve(context_id, "dev")
    return pr_view.pr_file_diff(ctx, context_id, item_id, path=path, task=task,
                                dev=dev, spine=spine)


@router.post("/dev/work-items/{item_id}/git/revert", response_model=GitRevertResponse)
async def dev_work_item_git_revert(item_id: str, body: GitBody,
                                   dev: DevKnowledgeService = Depends(get_dev),
                                   dev_store: DevStore = Depends(get_dev_store),
                                   spine: SystemSpine = Depends(get_spine)) -> dict:
    """Restore the trunk to its pre-merge state via the item's recorded backup ref.

    Safe-only: refuses once anything else has landed on top. The branch itself is untouched."""
    ctx, dev_root, item = _load(body.context_id, item_id, dev)
    backup = item.get("git_backup_ref")
    if not backup:
        raise HTTPException(status_code=409, detail="item has no recorded backup ref (no main merge to revert)")
    try:
        res = git_layer.revert_merge(ctx.cwd, backup, target=git_ops.repo_anchor(ctx, spine))
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
    """Resolve-with-agent: the human decides WHETHER, the agent resolves, and nobody hand-edits conflict
    markers.

    Re-runs the sync leaving conflicts in the worktree, then fires a background resolution run. The
    daemon completes the merge mechanically."""
    ctx, _root, item = _load(body.context_id, item_id, dev)
    wt = Path(_require_worktree(item))
    if spine.is_item_running(body.context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    try:
        res = git_layer.sync_from_main(ctx.cwd, wt, target=git_ops.repo_anchor(ctx, spine),
                                       leave_conflicts=True)
    except (git_layer.GitError, git_layer.GitBusy) as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not res.get("conflicts"):
        raise HTTPException(status_code=409,
                            detail="nothing to resolve — the sync merged cleanly (or was up to date)")
    model = spine.effective_model(body.context_id, item_model=item.get("model"))
    effort = spine.effective_effort(body.context_id, item_effort=item.get("effort"))
    if not begin_run(ctx, body.context_id, item_id, "resolve", model, phase=item.get("phase")):
        # Undo the in-tree conflict state before refusing — never leave a mess behind a 409.
        git_layer._git(wt, "merge", "--abort", check=False)
        raise HTTPException(status_code=409, detail="a run is already in progress for this item")
    asyncio.create_task(run_background_resolve(
        ctx, body.context_id, item_id, wt, res["conflicts"], model, effort))
    return {"ok": True, "status": "resolving", "id": item_id, "conflicts": res["conflicts"]}
