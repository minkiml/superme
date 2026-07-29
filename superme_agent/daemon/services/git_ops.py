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
from ...core import autopilot as _autopilot
from ...core.spine import REVIEW_MODE_DEFAULT

log = logging.getLogger("superme-agent")


def repo_anchor(ctx, spine) -> str | None:
    """This repo's configured `anchor_branch`, or None to let git_layer derive the default branch.
    One reader for every git site, so the setting can never be honoured in one place and ignored in
    another. Never raises: an unknown repo (a context synthesized without a registry entry) simply
    has no override."""
    rc = spine.repo(ctx.id)
    return rc.anchor_branch if rc else None


def repo_review_mode(ctx, spine) -> str:
    """This repo's review mode — `fast` (approve merges) | `strict` (approve opens a PR; the owner's
    approve on the PR page merges). Read LIVE at every decision point, never cached on the item: the
    mode describes the repo's bar today, so flipping it applies to items already sitting at review."""
    rc = spine.repo(ctx.id)
    return rc.review_mode if rc else REVIEW_MODE_DEFAULT


def pr_open(item: dict) -> bool:
    """Is this item's PR open — the deputy has approved and the merge is the OWNER's act? Derived
    from the two facts that decide it (stamped ∧ not yet merged), so it can never disagree with the
    git record: the merge itself is what closes the PR, and nothing has to remember to clear a
    flag."""
    return bool(item.get("git_pr_opened_at")) and not item.get("git_merge_commit")


def close_pr(dev, dev_root, item_id: str) -> None:
    """Leaving `review` WITHOUT merging closes the PR, because the approval is spent: the work it
    described is no longer the work. Both ways back out are the same act — a `revise` routing the
    item to plan, and the freshness rule sending it for one vet cycle — so both call this.

    Without it the stamp outlives the diff it approved: `pr_open` would stay true through the
    rework (offering the owner a Merge button on a half-built branch, at a phase where Approve
    doesn't merge at all), and `open_pr`'s first-time guard would then swallow the NEXT approval —
    leaving the record insisting the merge was handed over at a moment that has been superseded.
    Clearing keeps one fact honest and lets the re-approval log its own `git.pr`."""
    dev.set_work_item_git(dev_root, item_id, git_pr_opened_at=None)


def open_pr(ctx, context_id: str, item_id: str, *, dev, dev_store) -> dict:
    """`strict`'s second gate opening: record that the diff is now the owner's to merge, and page
    them. Idempotent WITHIN one stay at review — the deputy re-judging the same diff re-pages
    without moving the timestamp, so 'when did this land on my desk' stays the truth. An item that
    LEFT review had its stamp cleared by `close_pr`, so its next approval opens a genuinely new PR.

    This is deliberately NOT a merge and NOT a phase advance: the item stays at `review`, which is
    what makes the owner's approve the thing that lands the code."""
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id) or {}
    first = not item.get("git_pr_opened_at")
    if first:
        dev.set_work_item_git(dev_root, item_id,
                              git_pr_opened_at=datetime.now().isoformat(timespec="seconds"))
    dev.set_work_item_status(dev_root, item_id, "awaiting_human")
    if first:
        dev_store.log_event(context_id, "git.pr",
                            "PR opened — this repo is `strict`, so the merge is yours: read the "
                            "diff on the PR page and approve there",
                            item_id=item_id, actor="daemon",
                            meta={"branch": item.get("git_branch"), "review_mode": "strict"})
    return {"ok": True, "id": item_id, "phase": "review", "from": "review", "pr_open": True}


_DEFAULT_COMMIT_TYPE = "feat"


def _delivered_line(item_dir: Path) -> str:
    """The review report's **Delivered** field — what actually shipped, which is the right body for
    the landing commit. (§2.3 named an "Outcome" line; the 4a report has no such field, and
    `Recommendation` would put the literal word "merge" in the project's history.)

    Reads the whole PARAGRAPH, not the first physical line: report-review.md is hand-written prose
    wrapped for reading, so a two-sentence Delivered routinely spans three lines. Taking only the
    first cut the commit body off mid-sentence — in the one artifact of this item that outlives the
    workspace. Joined into one line here; `compose_commit` re-wraps it at 72."""
    try:
        report = item_dir / "reports" / "report-review.md"
        if not report.is_file():
            return ""
        parts: list[str] = []
        for line in report.read_text().splitlines():
            if not parts:
                if line.strip().startswith("**Delivered:**"):
                    parts.append(line.split("**Delivered:**", 1)[1].strip())
                continue
            # The field ends where its paragraph does — a blank line, or the next bold field for a
            # report whose author forgot the blank.
            if not line.strip() or line.strip().startswith("**"):
                break
            parts.append(line.strip())
        return " ".join(p for p in parts if p).strip()
    except OSError:
        log.warning("commit message: could not read report-review.md in %s", item_dir)
    return ""


def item_trailers(item: dict, item_id: str) -> dict:
    """The SuperMe facts that ride BELOW a commit's main message, in git trailer form. This is the
    only place item ids and workspace vocabulary are allowed to touch a commit — everything above
    is written for a reader who has never heard of this workspace."""
    sf = item.get("spawned_from") or {}
    return {
        "SuperMe-Item": item_id,
        "SuperMe-Parent": str(sf.get("item")) if isinstance(sf, dict) and sf.get("item") else "",
    }


def declared_commit(dev_store, context_id: str, item_id: str) -> dict | None:
    """The `machine.commit` the REVIEW run declared, read back from its `run.report` event.

    Newest-first and first-match: a `revise` sends the item round again, and the last review to
    finish is the one that describes what is actually landing. Best-effort — a missing declaration
    is a fallback, never a failed merge."""
    try:
        for e in dev_store.list_events(context_id, item_id=item_id, limit=40):
            if str(e.get("kind")) != "run.report":
                continue
            spec = ((e.get("meta") or {}).get("machine") or {}).get("commit")
            if isinstance(spec, dict) and spec.get("type") and spec.get("subject"):
                return spec
    except Exception:
        log.exception("declared commit read failed for %s", item_id)
    return None


def squash_message(item: dict, item_id: str, item_dir: Path, declared: dict | None = None) -> str:
    """The landing commit's message, assembled by the KERNEL from a DECLARED spec — the one
    artifact of this item that outlives the workspace.

    `declared` is `machine.commit` from the review run's completion payload: the review agent is
    the last phase that knows what actually shipped, so it declares `{type, subject}` (validated at
    the tool: four types, ≤50 chars, capitalized, no trailing period) and the kernel writes it.
    Absent — an older item, or a review that predates the field — falls back to the item title
    under the default type, which is honest about being a fallback rather than a guess dressed up
    as a classification."""
    if declared and declared.get("type") and declared.get("subject"):
        subject = f"{declared['type']}: {declared['subject']}"
    else:
        subject = f"{_DEFAULT_COMMIT_TYPE}: {str(item.get('title') or 'work item').strip()}"
    return git_layer.compose_commit(subject, _delivered_line(item_dir),
                                    item_trailers(item, item_id))


def build_downstream_digest(item_dir: Path, *, char_cap: int = 2400) -> str | None:
    """Assemble the 'what happened downstream' context a review→plan re-plan needs (deputy-live-turns
    Q1-B): the readiness snapshot (built + validated + warnings, authored at review entry) + the
    latest vet report's findings. This is what lets the plan phase know it's feedback from the earlier
    plan's BUILD results, not re-plan blind. Read-only; None when there's genuinely nothing to report
    (a review reached with no readiness and no vet — the feedback then stands alone)."""
    parts: list[str] = []
    try:
        review_report = item_dir / "reports" / "report-review.md"
        if review_report.is_file():
            body = review_report.read_text().strip()
            if body:
                parts.append("Readiness snapshot (built + validated at review entry):\n"
                             + body[:char_cap])
    except Exception:
        log.exception("digest: review-report read failed for %s", item_dir)
    try:
        vr = artifacts.latest_cycle_report(item_dir, char_cap=char_cap)
        if vr and (vr.get("text") or "").strip():
            parts.append(f"Latest cycle report (build-vet-{vr['cycle']}.md):\n{vr['text'].strip()}")
    except Exception:
        log.exception("digest: cycle report read failed for %s", item_dir)
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
    # Throwaway prompt-extraction probe: never merge (and never apply its knowledge delta to the
    # shared anchor docs — both live BELOW this early return). Return a SYNTHETIC merged=True so the
    # caller (advance_item) still advances review → close, letting the close prompt get captured
    # before the item is torn down. The real code/knowledge changes ride the disposable worktree,
    # which is deleted at cleanup — main and the anchor docs are never touched.
    if _autopilot.is_prompt_extraction(item):
        dev_store.log_event(context_id, "git.merge",
                            "Skipped merge (throwaway prompt-extraction probe — never lands on main)",
                            item_id=item_id, actor="daemon", meta={"skipped": "prompt_extraction"})
        return {"ok": True, "merged": True, "path": "prompt-extraction-skip",
                "skipped": "prompt_extraction", "lint_warnings": None}
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
    item_dir = dev_root / "work-items" / item_id
    # Freshness belongs to the merge, not to review (§2.3) — the anchor may have moved while this
    # item sat at the gate. Main path only: a blocking child lands on its PARENT's branch, where
    # the anchor's movement is the parent's problem to answer, not the child's.
    if not parent.get("git_worktree"):
        fresh = git_layer.merge_freshness(ctx.cwd, Path(str(item.get("git_worktree") or "")),
                                          branch, target=repo_anchor(ctx, spine))
        if fresh["action"] != "merge":
            dev_store.log_event(context_id, "git.freshness",
                                (f"Anchor moved and conflicts with this branch "
                                 f"({len(fresh.get('conflicts') or [])} file(s)) — held at review"
                                 if fresh["action"] == "park" else
                                 f"Anchor moved over {len(fresh.get('paths') or [])} file(s) this "
                                 f"item also changed — re-verifying before merge"),
                                item_id=item_id, actor="daemon", meta=fresh)
            return {"ok": True, "merged": False, "path": "main", "freshness": fresh["action"],
                    "conflicts": fresh.get("conflicts"), "stale_paths": fresh.get("paths"),
                    "lint_warnings": None}
    try:
        if parent.get("git_worktree"):
            # A branch-off child lands on its parent's branch and is squashed to the anchor LATER,
            # inside the parent's own merge — so it declares its commit exactly like any other
            # item, and the same assembler writes it.
            res = git_layer.merge_into_parent(
                ctx.cwd, branch, parent["git_worktree"],
                message=squash_message(item, item_id, item_dir,
                                       declared_commit(dev_store, context_id, item_id)))
            path = "parent"
        else:
            res = git_layer.merge_to_main(ctx.cwd, ctx.id, item_id, branch,
                                          target=repo_anchor(ctx, spine),
                                          merged_commit=item.get("git_merge_commit"),
                                          message=squash_message(
                                              item, item_id, item_dir,
                                              declared_commit(dev_store, context_id, item_id)))
            path = "main"
    except (git_layer.GitError, git_layer.GitBusy) as e:
        raise HTTPException(status_code=409, detail=str(e))
    lint_warnings = None
    if res.get("merged"):
        dev.set_work_item_git(dev_root, item_id,
                              git_merge_commit=res["merge_commit"],
                              git_merged_at=datetime.now().isoformat(timespec="seconds"),
                              **({"git_backup_ref": res["backup_ref"]}
                                 if res.get("backup_ref") else {}))
    # The merge no longer WRITES general knowledge (renovation §2.3, 2026-07-30): CLOSE is its sole
    # author, and close runs after this act locks the code. The stage-at-build / apply-at-merge
    # pair is retired with the whole crash-between-the-two recovery window it needed. What survives
    # is the standing freshness lint — truth decay detected at every merge, never just avoided.
    if (res.get("merged") or res.get("already_merged")) and path == "main":
        lint_warnings = knowledge_delta.freshness_lint(dev_root, ctx.cwd) or None
        if lint_warnings:
            dev_store.log_event(context_id, "knowledge.lint",
                                f"Freshness lint: {len(lint_warnings)} warning(s) after merge",
                                item_id=item_id, actor="daemon",
                                meta={"warnings": lint_warnings})
    dev_store.log_event(context_id, "git.merge",
                        (f"Merged to {res.get('target') or 'trunk'}" if res.get("merged")
                         else "Already merged" if res.get("already_merged")
                         else f"Merge hit {len(res.get('conflicts') or [])} conflict(s)"),
                        item_id=item_id, actor="owner", meta={**res, "path": path})
    return {"ok": True, "merged": bool(res.get("merged")), "path": path,
            "lint_warnings": lint_warnings,
            **{k: v for k, v in res.items() if k != "merged"}}
