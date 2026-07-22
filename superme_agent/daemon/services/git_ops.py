"""The review-gate merge, as a service both callers share (B2 fix, 2026-07-20).

'The review decision IS the merge' (build-vet-loop) — so leaving the review gate and merging the
branch are ONE act, not two endpoints the owner must fire in sequence. Before this, the FE Approve
called `/advance` only; the item landed in `close` UNMERGED, and `/git/merge` then refused (it is
review-only), stranding the item. Now `advance_item` (gates.py) calls `review_merge` on the
review→close transition, and the `/git/merge` route is a thin wrapper over the same body — so an
owner click, a deputy approval, and the raw route all merge identically.

`review_merge` re-reads the item (authoritative), enforces the same refusals the route always did,
routes by topology (light blocking-child path / heavy trunk path), applies the paired knowledge
delta, and returns the merge result dict. A conflict comes back as `merged=False` with a conflict
list (NOT an exception) so the caller can hold the item at review instead of advancing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from ...core import artifacts, git_layer, knowledge_delta

log = logging.getLogger("superme-agent")


def write_review_readiness(ctx, item: dict, item_dir, dev_root) -> None:
    """Author readiness.md at review entry (B3), best-effort. Gathers the git diff-stat + freshness
    debt + the staged knowledge delta, then hands them to artifacts.author_readiness so the review
    deputy/owner (and, later, the PR body) has a real report instead of a missing file. NEVER raises
    — a failure just means a review session / the owner authors it (the pre-B3 status quo)."""
    try:
        wt = item.get("git_worktree")
        git_stats, behind = None, 0
        if wt and Path(str(wt)).is_dir():
            health = git_layer.worktree_health(ctx.cwd, ctx.id, str(item["id"]),
                                               item.get("git_branch"))
            behind = int(health.get("behind") or 0)
            base = item.get("git_base") or health.get("trunk")
            if base:
                git_stats = git_layer.diff_numstat(Path(str(wt)), str(base))
        delta = knowledge_delta.pending_delta(item_dir)
        ops = (delta or {}).get("ops") or []
        delta_summary = f"{len(ops)} knowledge op(s) staged for merge." if ops else None
        repo_dir = Path(str(wt)) if wt else ctx.cwd
        artifacts.author_readiness(
            item_dir, repo_dir,
            title=item.get("title") or str(item["id"]),
            delta_summary=delta_summary, git_stats=git_stats, behind=behind)
        # The review gate check also requires the styled gate report; render it mechanically here
        # (its visual sibling), else an autopilot item reaches review with the report missing and
        # the deputy escalates 100% of clean items. A review session may enrich it later.
        artifacts.author_review_report(
            item_dir, repo_dir,
            title=item.get("title") or str(item["id"]),
            git_stats=git_stats, behind=behind)
    except Exception:
        log.exception("write_review_readiness failed for %s (review authors it instead)",
                      item.get("id"))


def build_downstream_digest(item_dir: Path, *, char_cap: int = 2400) -> str | None:
    """Assemble the 'what happened downstream' context a review→plan re-plan needs (deputy-live-turns
    Q1-B): the readiness snapshot (built + validated + warnings, authored at review entry) + the
    latest vet report's findings. This is what lets the plan phase know it's feedback from the earlier
    plan's BUILD results, not re-plan blind. Read-only; None when there's genuinely nothing to report
    (a review reached with no readiness and no vet — the feedback then stands alone)."""
    parts: list[str] = []
    try:
        readiness = item_dir / "artifacts" / artifacts.artifact_file("readiness")
        if readiness.is_file():
            body = readiness.read_text().strip()
            if body:
                parts.append("Readiness snapshot (built + validated at review entry):\n"
                             + body[:char_cap])
    except Exception:
        log.exception("digest: readiness read failed for %s", item_dir)
    try:
        vr = artifacts.latest_vet_report(item_dir, char_cap=char_cap)
        if vr and (vr.get("text") or "").strip():
            parts.append(f"Latest vet report (cycle {vr['cycle']}):\n{vr['text'].strip()}")
    except Exception:
        log.exception("digest: vet report read failed for %s", item_dir)
    return "\n\n".join(parts) if parts else None


def review_merge(ctx, context_id: str, item_id: str, *, dev, dev_store, spine) -> dict:
    """Perform the review-gate merge for `item_id` and return the result dict (same shape the
    `/git/merge` route returns). Raises HTTPException on the shared refusals (not review-phase,
    no branch, run in flight, blocking-parent unmerged, invalid knowledge delta). A merge CONFLICT
    is a normal return (`merged=False`, `conflicts=[…]`), never a raise — the caller decides
    whether to hold at review (advance) or just report (route)."""
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    # merge is the REVIEW gate's action — the owner's decision IS the merge (decide=execute).
    # Pre-review the vet bar hasn't been met, so an ungated merge would land unvetted work on main.
    if str(item.get("phase")) != "review":
        raise HTTPException(
            status_code=409,
            detail=f"merge is a review-gate action — this item is in `{item.get('phase')}`. "
                   "Drive it to the review gate first (the review decision IS the merge).")
    branch = item.get("git_branch")
    if not branch:
        raise HTTPException(status_code=409, detail="item has no branch (created on build entry)")
    if spine.is_item_running(context_id, item_id):
        raise HTTPException(status_code=409, detail="a run is in progress for this item")
    # Light path? — blocking child whose parent still holds a live worktree.
    sf = item.get("spawned_from") or {}
    parent = (dev.read_work_item(dev_root, str(sf.get("item"))) or {}) \
        if isinstance(sf, dict) and sf.get("relation") == "blocking" else {}
    # A blocking child BRANCHED FROM its parent's branch — its ancestry contains every parent
    # commit made before the branch-off. With the parent's worktree gone (abandoned / cleaned) the
    # light path is unavailable, and falling through to a trunk merge would land the parent's
    # UNREVIEWED half-finished work on main. Refuse unless the parent itself merged.
    if isinstance(sf, dict) and sf.get("relation") == "blocking" \
            and parent and not parent.get("git_worktree") and not parent.get("git_merge_commit"):
        raise HTTPException(
            status_code=409,
            detail="blocking child's parent has no worktree and was never merged — merging this "
                   "child to trunk would carry the parent's unfinished commits. Re-enter the "
                   "parent's build (its branch is kept) or abandon this child with it.")
    # D7 pre-merge validation: a staged knowledge delta is re-validated against the tree that is
    # ABOUT TO BECOME main (the item's worktree — freshness-synced, so its content is the merge
    # result). An invalid delta REFUSES the merge — a doc can never acquire a dead pointer.
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
    # window: the code is on main but the knowledge write never happened. Apply is idempotent, so
    # the retry completes the pair (else the close gate's knowledge_row_resolved fails with no
    # recovery route).
    merged_now_or_before = res.get("merged") or res.get("already_merged")
    if merged_now_or_before:
        # D7: merge + knowledge-write = ONE "codebase changed" event.
        if path == "main":
            if delta:
                applied = knowledge_delta.apply_delta(item_dir, dev_root)
                knowledge_ops_applied = applied.get("applied", 0)
                dev_store.log_event(context_id, "knowledge.applied",
                                    f"Applied knowledge delta ({knowledge_ops_applied} op(s) → "
                                    f"{', '.join(applied.get('docs', []))}) with the merge",
                                    item_id=item_id, actor="daemon", meta=applied)
            # Standing freshness lint — truth decay detected at every merge, never just avoided.
            lint_warnings = knowledge_delta.freshness_lint(dev_root, ctx.cwd) or None
            if lint_warnings:
                dev_store.log_event(context_id, "knowledge.lint",
                                    f"Freshness lint: {len(lint_warnings)} warning(s) after merge",
                                    item_id=item_id, actor="daemon",
                                    meta={"warnings": lint_warnings})
        elif delta:  # blocking child → its delta FOLDS into the parent's staged delta
            parent_id = str(sf.get("item"))
            moved = knowledge_delta.fold_into_parent(
                item_dir, dev_root / "work-items" / parent_id, parent_id)
            if moved:
                knowledge_folded_into = parent_id
                dev_store.log_event(context_id, "knowledge.folded",
                                    f"Folded {moved} knowledge op(s) into parent {parent_id}",
                                    item_id=item_id, actor="daemon",
                                    meta={"parent": parent_id, "ops": moved})
    dev_store.log_event(context_id, "git.merge",
                        (f"Merged to {res.get('target') or 'trunk'}" if res.get("merged")
                         else "Already merged" if res.get("already_merged")
                         else f"Merge hit {len(res.get('conflicts') or [])} conflict(s)"),
                        item_id=item_id, actor="owner", meta={**res, "path": path})
    return {"ok": True, "merged": bool(res.get("merged")), "path": path,
            "knowledge_ops_applied": knowledge_ops_applied,
            "knowledge_folded_into": knowledge_folded_into,
            "lint_warnings": lint_warnings,
            **{k: v for k, v in res.items() if k != "merged"}}
