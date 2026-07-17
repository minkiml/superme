"""Work-item git routes (workspace-workflow S4/D4): health check, freshness sync, the deliver
merge (heavy main path / light blocking-child path), backup-ref revert, and Resolve-with-Agent.

These are the OWNER's git surface — mechanics only; the readiness brief that fronts the deliver
decision lands at S6. The mechanics themselves live in core/git_layer; item records in
core/dev_knowledge. Every route re-reads the item (authoritative state, never a stored flag).
"""

import asyncio
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...app_state import DevKnowledgeService, DevStore, SystemSpine, get_dev, get_dev_store, get_spine
from ....core import git_layer, knowledge_delta
from ....gateway import contexts
from ...services.runs import _begin_run, _run_headless_resolve
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
    during long builds and always before the deliver merge, so main-merge is trivial."""
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
    """The deliver-gate merge (owner-fired). Routes itself by topology (D4): a BLOCKING child
    merges into its parent's branch (light path — no backup ceremony; the parent re-validates the
    family), everything else merges to the trunk (heavy path — overlap refusal, tagged auto-stash,
    backup ref first, never-merge-twice). Success records merge commit (+ backup ref) on the item.
    Conflicts on the main path → 200 with the conflict list (fix via sync + resolve, then retry)."""
    ctx, dev_root, item = _load(body.context_id, item_id, dev)
    branch = item.get("git_branch")
    if not branch:
        raise HTTPException(status_code=409, detail="item has no branch (created on build entry)")
    if spine.is_item_running(body.context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    # Light path? — blocking child whose parent still holds a live worktree.
    sf = item.get("spawned_from") or {}
    parent = (dev.read_work_item(dev_root, str(sf.get("item"))) or {}) \
        if isinstance(sf, dict) and sf.get("relation") == "blocking" else {}
    # A blocking child BRANCHED FROM its parent's branch — its ancestry contains every parent
    # commit made before the branch-off. With the parent's worktree gone (abandoned / cleaned)
    # the light path is unavailable, and falling through to a trunk merge would land the
    # parent's UNREVIEWED half-finished work on main. Refuse unless the parent itself merged.
    if isinstance(sf, dict) and sf.get("relation") == "blocking" \
            and parent and not parent.get("git_worktree") and not parent.get("git_merge_commit"):
        raise HTTPException(
            status_code=409,
            detail="blocking child's parent has no worktree and was never merged — merging this "
                   "child to trunk would carry the parent's unfinished commits. Re-enter the "
                   "parent's build (its branch is kept) or abandon this child with it.")
    # D7 pre-merge validation: a staged knowledge delta is re-validated against the tree that is
    # ABOUT TO BECOME main (the item's worktree — freshness-synced, so its content is the merge
    # result). An invalid delta (dead file ref, vanished section, ghost slug) REFUSES the merge —
    # a doc can never acquire a dead pointer, and the failure is itemized + retry-shaped.
    item_dir = dev_root / "work-items" / item_id
    delta = knowledge_delta.pending_delta(item_dir)
    if delta and not parent.get("git_worktree"):
        wt_dir = item.get("git_worktree")
        ref_repo = Path(str(wt_dir)) if wt_dir and Path(str(wt_dir)).is_dir() else ctx.cwd
        issues = knowledge_delta.validate_ops(delta["ops"], dev_root, ref_repo)
        if issues:
            raise HTTPException(status_code=409,
                                detail="knowledge delta invalid — fix and restage before "
                                       "merging: " + "; ".join(issues))
    try:
        if parent.get("git_worktree"):
            res = git_layer.merge_into_parent(ctx.cwd, branch, parent["git_worktree"])
            path = "parent"
        else:
            res = git_layer.merge_to_main(ctx.cwd, ctx.id, item_id, branch)
            path = "main"
    except (git_layer.GitError, git_layer.GitBusy) as e:
        raise HTTPException(status_code=409, detail=str(e))
    knowledge_ops_applied = None
    knowledge_folded_into = None
    lint_warnings = None
    if res.get("merged"):
        dev.set_work_item_git(dev_root, item_id,
                              git_merge_commit=res["merge_commit"],
                              git_merged_at=datetime.now().isoformat(timespec="seconds"),
                              **({"git_backup_ref": res["backup_ref"]}
                                 if res.get("backup_ref") else {}))
    # A still-PENDING delta on an `already_merged` retry is the crash-between-merge-and-apply
    # window (or an apply that raised after the merge landed): the code is on main but the
    # knowledge write never happened, and skipping it here would strand the delta forever —
    # the close gate's knowledge_row_resolved would then fail with no recovery route. Apply is
    # idempotent (pending-status guarded + re-validated above), so the retry completes the pair.
    merged_now_or_before = res.get("merged") or res.get("already_merged")
    if merged_now_or_before:
        # D7: merge + knowledge-write = ONE "codebase changed" event.
        if path == "main":
            if delta:
                applied = knowledge_delta.apply_delta(item_dir, dev_root)
                knowledge_ops_applied = applied.get("applied", 0)
                dev_store.log_event(body.context_id, "knowledge.applied",
                                    f"Applied knowledge delta ({knowledge_ops_applied} op(s) → "
                                    f"{', '.join(applied.get('docs', []))}) with the merge",
                                    item_id=item_id, actor="daemon", meta=applied)
            # Standing freshness lint — truth decay detected at every merge, never just avoided.
            lint_warnings = knowledge_delta.freshness_lint(dev_root, ctx.cwd) or None
            if lint_warnings:
                dev_store.log_event(body.context_id, "knowledge.lint",
                                    f"Freshness lint: {len(lint_warnings)} warning(s) after merge",
                                    item_id=item_id, actor="daemon",
                                    meta={"warnings": lint_warnings})
        elif delta:  # blocking child → its delta FOLDS into the parent's staged delta
            parent_id = str(sf.get("item"))
            moved = knowledge_delta.fold_into_parent(
                item_dir, dev_root / "work-items" / parent_id, parent_id)
            if moved:
                knowledge_folded_into = parent_id
                dev_store.log_event(body.context_id, "knowledge.folded",
                                    f"Folded {moved} knowledge op(s) into parent {parent_id}",
                                    item_id=item_id, actor="daemon",
                                    meta={"parent": parent_id, "ops": moved})
    dev_store.log_event(body.context_id, "git.merge",
                        (f"Merged to {res.get('target') or 'trunk'}" if res.get("merged")
                         else "Already merged" if res.get("already_merged")
                         else f"Merge hit {len(res.get('conflicts') or [])} conflict(s)"),
                        item_id=item_id, actor="owner", meta={**res, "path": path})
    return {"ok": True, "merged": bool(res.get("merged")), "path": path,
            "knowledge_ops_applied": knowledge_ops_applied,
            "knowledge_folded_into": knowledge_folded_into,
            "lint_warnings": lint_warnings,
            **{k: v for k, v in res.items() if k != "merged"}}


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
    then fires a headless resolution run (write-sandboxed to the worktree); the daemon completes
    the merge mechanically and the item re-enters `validate`. 409 if the sync is clean (nothing
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
    asyncio.create_task(_run_headless_resolve(
        ctx, body.context_id, item_id, wt, res["conflicts"], model, effort))
    return {"ok": True, "status": "resolving", "id": item_id, "conflicts": res["conflicts"]}
