"""The review-gate merge, shared by both callers.

Leaving the review gate and merging the branch are ONE act, so `advance_item` and the
`/git/merge` route share one body.

A conflict returns `merged=False` with a conflict list, never an exception.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from ...core import artifacts, git_layer, kind_profiles, knowledge_delta
from ...core import autopilot as _autopilot
from ...core.spine import REVIEW_MODE_DEFAULT

log = logging.getLogger("superme-agent")


def repo_anchor(ctx, spine) -> str | None:
    """This repo's configured `anchor_branch`, or None to let git_layer derive the default. One reader
    for every git site."""
    rc = spine.repo(ctx.id)
    return rc.anchor_branch if rc else None


def repo_review_mode(ctx, spine) -> str:
    """This repo's review mode. Read LIVE at every decision point, never cached on the item, so flipping
    it applies to items already at review."""
    rc = spine.repo(ctx.id)
    return rc.review_mode if rc else REVIEW_MODE_DEFAULT


# An allowlisted path says what the owner considers source, not that every byte under it is.
_NOT_SOURCE = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                         "node_modules", ".git", ".venv", "venv", ".DS_Store"})
_NOT_SOURCE_SUFFIX = (".pyc", ".pyo", ".so", ".dylib", ".db", ".sqlite", ".sqlite3", ".log")
# The floor under the allowlist, not a rule it can lift — a mis-named path must never expose a
# credential.
_NEVER_SOURCE_SUFFIX = (".pem", ".key", ".p12", ".pfx", ".keystore")


def _is_secret(name: str) -> bool:
    low = name.lower()
    return (low == ".env" or low.startswith(".env.") or low.endswith(_NEVER_SOURCE_SUFFIX)
            or low.startswith(("id_rsa", "id_ed25519", "credentials", ".netrc", ".npmrc",
                               ".pypirc")))


def _mirror_source_ignored(repo: Path, worktree: Path, paths: list[str]) -> tuple[int, list[str]]:
    """Copy the owner-named gitignored source paths into a fresh scratch worktree.

    A worktree holds tracked files only, so a completeness claim cannot be proven in it. Copies are
    mode 0o444: edits here would be invisible.

    Returns `(files_copied, skipped_reasons)`; never raises."""
    copied, skipped = 0, []
    for rel in paths:
        src, dst = repo / rel, worktree / rel
        try:
            if not src.exists():
                skipped.append(f"{rel}: not present in the repo")
                continue
            if _is_secret(Path(rel).name):
                skipped.append(f"{rel}: refused — this looks like a secret, and the allowlist "
                               f"cannot lift that")
                continue
            if src.is_file():
                if dst.exists():
                    skipped.append(f"{rel}: already in the checkout")
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                dst.chmod(0o444)
                copied += 1
                continue
            # Merge into the directory rather than skipping it: a partly-tracked dir would
            # otherwise be skipped entirely.
            for cur, dirs, files in os.walk(src):
                dirs[:] = [d for d in dirs if d not in _NOT_SOURCE]
                target = dst / Path(cur).relative_to(src)
                target.mkdir(parents=True, exist_ok=True)
                for f in files:
                    if f in _NOT_SOURCE or f.endswith(_NOT_SOURCE_SUFFIX) or _is_secret(f):
                        continue
                    out = target / f
                    if out.exists():        # tracked: the checkout's copy is authoritative
                        continue
                    shutil.copy2(Path(cur) / f, out)
                    out.chmod(0o444)
                    copied += 1
            # Directories stay writable: unlinking depends on the DIRECTORY's write bit, so read-
            # only dirs broke disposal.
        except Exception as e:                                  # noqa: BLE001 — never fatal
            skipped.append(f"{rel}: {e}")
    return copied, skipped


def ensure_scratch_worktree(ctx, context_id: str, item: dict, *, dev, dev_store, spine) -> Path:
    """The isolated tree a READ-ONLY kind reads from, created on demand. Falls back to `ctx.cwd` on any
    failure.

    LAZY rather than gate-driven: a sweep-launched item is minted straight at `investigate` and never
    passes `advance_item`. Idempotent."""
    kind = str(item.get("kind") or "")
    if not kind_profiles.get_profile(kind).scratch_worktree:
        return ctx.cwd
    item_id = str(item.get("id") or "")
    recorded = str(item.get("git_worktree") or "")
    if recorded and Path(recorded).is_dir():
        return Path(recorded)
    try:
        rec = git_layer.create_scratch_worktree(ctx.cwd, ctx.id, item_id,
                                                base=repo_anchor(ctx, spine))
    except (git_layer.GitError, git_layer.GitBusy) as e:
        # Never fatal — a repo that is not a git repository is common, and research reads fine
        # without isolation.
        log.warning("scratch worktree unavailable for %s — reading the live repo: %s", item_id, e)
        return ctx.cwd
    dev.set_work_item_git(ctx.internal_root / "dev", item_id,
                          git_worktree=rec["worktree"], git_base=rec["base"])
    if not rec.get("reused"):
        # Mirror before the tree is announced, so the first run to arrive reads a complete one.
        # Fresh trees only.
        cfg = spine.repo(ctx.id)
        named = list(getattr(cfg, "source_ignored", None) or [])
        copied, skipped = _mirror_source_ignored(ctx.cwd, Path(rec["worktree"]), named) \
            if named else (0, [])
        dev_store.log_event(context_id, "git.worktree",
                            f"Created a detached read-only checkout at {rec['base']}"
                            + (f" · mirrored {copied} file(s) from {len(named)} ignored source "
                               f"path(s)" if named else ""),
                            item_id=item_id, actor="daemon",
                            meta={**rec, **({"source_ignored": named, "mirrored": copied}
                                            if named else {}),
                                  **({"mirror_skipped": skipped} if skipped else {})})
        if skipped:
            log.warning("scratch worktree %s: source_ignored not fully mirrored — %s",
                        item_id, "; ".join(skipped))
    return Path(rec["worktree"])


def pr_open(item: dict) -> bool:
    """Is this item's PR open — the deputy approved and the merge is the OWNER's act? Derived from
    stamped and not-yet-merged, so nothing has to clear a flag."""
    return bool(item.get("git_pr_opened_at")) and not item.get("git_merge_commit")


def close_pr(dev, dev_root, item_id: str) -> None:
    """Leaving `review` WITHOUT merging closes the PR, because the approval is spent: the work it
    described is no longer the work.

    Both ways back call this. Without it the stamp outlives the diff it approved."""
    dev.set_work_item_git(dev_root, item_id, git_pr_opened_at=None)


def open_pr(ctx, context_id: str, item_id: str, *, dev, dev_store) -> dict:
    """`strict`'s second gate opening: record that the diff is now the owner's to merge, and page them.

    Idempotent within one stay at review, so re-judging the same diff does not move the timestamp.
    Not a merge, and not an advance."""
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id) or {}
    first = not item.get("git_pr_opened_at")
    if first:
        dev.set_work_item_git(dev_root, item_id,
                              git_pr_opened_at=datetime.now().isoformat(timespec="seconds"))
    dev.set_work_item_status(dev_root, item_id, "awaiting_human")
    if first:
        # Just the fact: the attention card directly beneath already says what to do about it.
        dev_store.log_event(context_id, "git.pr", "PR opened",
                            item_id=item_id, actor="daemon",
                            meta={"branch": item.get("git_branch"), "review_mode": "strict"})
    return {"ok": True, "id": item_id, "phase": "review", "from": "review", "pr_open": True}


_DEFAULT_COMMIT_TYPE = "feat"


def _delivered_line(item_dir: Path) -> str:
    """The **Delivered** field of `artifacts/review.md` — what shipped, and the right body for the
    landing commit.

    It reads the AGENT-facing record, not the owner's report: a field a machine parses belongs in the
    doc written for machines."""
    return artifacts.delivered_line(item_dir)


def item_trailers(item: dict, item_id: str) -> dict:
    """The SuperMe facts that ride below a commit's message, as git trailers — the only place item ids
    touch a commit."""
    sf = item.get("spawned_from") or {}
    return {
        "SuperMe-Item": item_id,
        "SuperMe-Parent": str(sf.get("item")) if isinstance(sf, dict) and sf.get("item") else "",
    }


def declared_commit(dev_store, context_id: str, item_id: str) -> dict | None:
    """The `machine.commit` the review run declared, read back from its `run.report` event.
    Newest-first: a `revise` sends the item round again. Best-effort."""
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
    """The landing commit's message, assembled by the KERNEL from a DECLARED spec.

    `declared` is `machine.commit` from the review run's payload: review is the last phase that knows
    what shipped. Absent, it falls back to the item title."""
    if declared and declared.get("type") and declared.get("subject"):
        subject = f"{declared['type']}: {declared['subject']}"
    else:
        subject = f"{_DEFAULT_COMMIT_TYPE}: {str(item.get('title') or 'work item').strip()}"
    return git_layer.compose_commit(subject, _delivered_line(item_dir),
                                    item_trailers(item, item_id))


def build_downstream_digest(item_dir: Path, *, char_cap: int = 2400) -> str | None:
    """The downstream context a review→plan re-plan needs: what landed, what it settled, and the latest
    cycle findings.

    It reads the AGENT-facing record, because a re-plan needs the settled decisions it may not
    silently re-open. None when there is nothing."""
    parts: list[str] = []
    try:
        review_record = item_dir / "artifacts" / artifacts.artifact_file("review")
        if review_record.is_file():
            body = review_record.read_text().strip()
            if body:
                head = body[:char_cap]
                parts.append("Review's record of the last pass (artifacts/review.md):\n" + head)
                # `Owner's decision` sits at the END of the record, so head-truncation drops it
                # exactly when the record is long.
                decision = artifacts.owner_decision(item_dir)
                if decision and decision not in head:
                    parts.append(f"Owner's decision at that review: {decision}")
    except Exception:
        log.exception("digest: review record read failed for %s", item_dir)
    try:
        vr = artifacts.latest_cycle_report(item_dir, char_cap=char_cap)
        if vr and (vr.get("text") or "").strip():
            parts.append(f"Latest cycle report (build-vet-{vr['cycle']}.md):\n{vr['text'].strip()}")
    except Exception:
        log.exception("digest: cycle report read failed for %s", item_dir)
    return "\n\n".join(parts) if parts else None


def review_merge(ctx, context_id: str, item_id: str, *, dev, dev_store, spine) -> dict:
    """Perform the review-gate merge and return the result dict. Raises HTTPException on the shared
    refusals; a CONFLICT is a normal return, never a raise."""
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    # Terminal first, before the phase test: a dropped item keeps the phase it died in and would
    # sail through below.
    if bool(item.get("done_at")) or str(item.get("status")) == "done":
        raise HTTPException(
            status_code=409,
            detail=f"this item is finished ({item.get('outcome') or 'done'}) — there is nothing "
                   f"left to decide. Its branch stays unmerged by that decision.")
    # The owner's decision IS the merge. Pre-review the vet bar is unmet, so an ungated merge
    # would land unvetted work.
    if str(item.get("phase")) != "review":
        raise HTTPException(
            status_code=409,
            detail=f"merge is a review-gate action — this item is in `{item.get('phase')}`. "
                   "Drive it to the review gate first (the review decision IS the merge).")
    # The probe never merges; a synthetic `merged=True` lets the caller advance review → close and
    # capture that prompt.
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
    # A blocking child branched from its parent's branch, so a trunk merge would land the parent's
    # unreviewed work.
    if isinstance(sf, dict) and sf.get("relation") == "blocking" \
            and parent and not parent.get("git_worktree") and not parent.get("git_merge_commit"):
        raise HTTPException(
            status_code=409,
            detail="blocking child's parent has no worktree and was never merged — merging this "
                   "child to trunk would carry the parent's unfinished commits. Re-enter the "
                   "parent's build (its branch is kept) or abandon this child with it.")
    item_dir = dev_root / "work-items" / item_id
    # Freshness belongs to the merge, not review: the anchor may have moved while the item sat at
    # the gate.
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
            # A branch-off child lands on its parent's branch and is squashed to the anchor inside
            # the parent's own merge.
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
    # Close is the sole author of general knowledge; what runs here is the standing freshness
    # lint.
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
