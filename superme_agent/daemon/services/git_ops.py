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


# Junk that lives INSIDE an otherwise-source directory. An allowlisted path is a statement about
# what the owner considers source, not a promise that every byte under it is — a Python package
# carries its own caches, and copying those buys an agent nothing but noise to read past.
_NOT_SOURCE = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
                         "node_modules", ".git", ".venv", "venv", ".DS_Store"})
_NOT_SOURCE_SUFFIX = (".pyc", ".pyo", ".so", ".dylib", ".db", ".sqlite", ".sqlite3", ".log")
# NEVER copied, whatever the config says. A secret is the one thing gitignore is most often
# protecting, and the cost of the owner mis-naming a path once is a credential sitting in a tree an
# agent reads and quotes. Refused loudly instead — this is the floor under the allowlist, not a
# rule the allowlist can lift.
_NEVER_SOURCE_SUFFIX = (".pem", ".key", ".p12", ".pfx", ".keystore")


def _is_secret(name: str) -> bool:
    low = name.lower()
    return (low == ".env" or low.startswith(".env.") or low.endswith(_NEVER_SOURCE_SUFFIX)
            or low.startswith(("id_rsa", "id_ed25519", "credentials", ".netrc", ".npmrc",
                               ".pypirc")))


def _mirror_source_ignored(repo: Path, worktree: Path, paths: list[str]) -> tuple[int, list[str]]:
    """Copy the owner-named gitignored SOURCE paths into a fresh scratch worktree, read-only.

    A worktree is a checkout, so it holds tracked files only. That is right for isolation and wrong
    for the one claim `housekeeping` exists to make — "nothing reaches this" cannot be proven in a
    tree that is missing files. This closes the gap for exactly the paths the owner named in
    `RepoConfig.source_ignored`, and for nothing else: the default is empty, and a repo that names
    nothing keeps today's behaviour.

    Copies are made **read-only** (mode 0o444 / dirs 0o555). The run is already denied writes
    outside its item folder by the permission layer; the file mode is the second wall, so a copy
    can never be mistaken for a place to edit — and edits here would be invisible, landing in a
    throwaway tree instead of the repo.

    Returns `(files_copied, skipped_reasons)`. Never raises: a mirror that fails leaves the run
    with today's incomplete tree, which is worse but not broken, and the skip is reported so the
    silence that caused this defect is not simply reintroduced one layer down.
    """
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
            # MERGE into the directory rather than skipping when it exists. The case that matters
            # is a partly-tracked dir — this repo's `scripts/` has 5 tracked files and ~40 ignored
            # ones — so skip-if-present would skip exactly the directory the defect was found in.
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
            # Directory modes come last: a read-only dir cannot be written into while walking it.
            for cur, dirs, _ in os.walk(dst, topdown=False):
                Path(cur).chmod(0o555)
        except Exception as e:                                  # noqa: BLE001 — never fatal
            skipped.append(f"{rel}: {e}")
    return copied, skipped


def ensure_scratch_worktree(ctx, context_id: str, item: dict, *, dev, dev_store, spine) -> Path:
    """The isolated tree a READ-ONLY kind reads from — created on demand, returned as the cwd its
    runs should use. Falls back to `ctx.cwd` (the live repo) for any kind that doesn't want one,
    and for any failure: an investigation that cannot run is worse than one running where it
    always ran, and the permission layer is still in front of it either way.

    LAZY rather than gate-driven, because a sweep-launched research item is minted straight at
    `investigate` (`create_work_item(phase="investigate")`) and never passes through `advance_item`
    — a hook on the transition would have covered the ticket-born half only. Every run of every
    phase comes through here instead, so the first one to arrive makes the tree and the rest reuse
    it. Idempotent: a recorded tree that still exists is returned untouched; one whose dir has been
    swept away is simply remade.
    """
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
        # Never fatal. A repo that is not a git repository at all is the common case here (the
        # playground was one for a while), and research reads fine without isolation.
        log.warning("scratch worktree unavailable for %s — reading the live repo: %s", item_id, e)
        return ctx.cwd
    dev.set_work_item_git(ctx.internal_root / "dev", item_id,
                          git_worktree=rec["worktree"], git_base=rec["base"])
    if not rec.get("reused"):
        # Mirror the owner-named gitignored source BEFORE the tree is announced, so the first run
        # to arrive already reads a complete one. Only on a fresh tree: a reused tree was mirrored
        # when it was made, and re-copying would fight the read-only modes it now carries.
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
        # Just the fact. This message is the Now line's tail ("Review · cycle 1 · <this>"), and the
        # attention card directly beneath it already says what to do about it — spelling out the
        # mode and the instruction here printed the same sentence twice on one screen.
        dev_store.log_event(context_id, "git.pr", "PR opened",
                            item_id=item_id, actor="daemon",
                            meta={"branch": item.get("git_branch"), "review_mode": "strict"})
    return {"ok": True, "id": item_id, "phase": "review", "from": "review", "pr_open": True}


_DEFAULT_COMMIT_TYPE = "feat"


def _delivered_line(item_dir: Path) -> str:
    """The **Delivered** field of `artifacts/review.md` — what actually shipped, which is the right
    body for the landing commit. (§2.3 named an "Outcome" line; the 4a report had no such field, and
    `Recommendation` would put the literal word "merge" in the project's history.)

    It reads the AGENT-facing review record, not the owner's report: the owner's report is prose
    written to be read once at a gate, and the commit body outlives the workspace by years. A field
    a machine parses belongs in the doc written for machines.

    ONE parse, shared with the PR guide's claim (`artifacts.delivered_line`): the project's
    permanent history and the document the merge was decided on read the same bytes, so they cannot
    come to say different things about what landed. Joined into one line there; `compose_commit`
    re-wraps at 72."""
    return artifacts.delivered_line(item_dir)


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
    Q1-B): review's own record of what landed and what it settled, plus the latest cycle report's
    findings. This is what lets the plan phase know it's feedback from the earlier plan's BUILD
    results, not re-plan blind. Read-only; None when there's genuinely nothing to report (a review
    reached with no record and no vet — the feedback then stands alone).

    It reads `artifacts/review.md`, the AGENT-facing record, not the owner's report. A re-plan needs
    the change inventory, the settled decisions it may not silently re-open, and the surviving risks
    — none of which the owner's report carries, because none of them are written for a human."""
    parts: list[str] = []
    try:
        review_record = item_dir / "artifacts" / artifacts.artifact_file("review")
        if review_record.is_file():
            body = review_record.read_text().strip()
            if body:
                parts.append("Review's record of the last pass (artifacts/review.md):\n"
                             + body[:char_cap])
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
    """Perform the review-gate merge for `item_id` and return the result dict (same shape the
    `/git/merge` route returns). Raises HTTPException on the shared refusals (not review-phase,
    no branch, run in flight, blocking-parent unmerged, invalid knowledge delta). A merge CONFLICT
    is a normal return (`merged=False`, `conflicts=[…]`), never a raise — the caller decides
    whether to hold at review (advance) or just report (route)."""
    dev_root = ctx.internal_root / "dev"
    item = dev.read_work_item(dev_root, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="work-item not found")
    # TERMINAL FIRST, before the phase test. A DROPPED item keeps the phase it died in — abandon
    # writes `status`/`done_at`/`outcome` and leaves `phase` alone — so an item abandoned AT review
    # still reads `phase == "review"` and sailed straight through the guard below. Live, 2026-08-14:
    # `f045c70a1aee` was abandoned with its PR open and its branch unmerged, and its PR page still
    # offered a live Merge that would have landed the work on main. Deciding is what a gate does;
    # a terminal item has already been decided, and "abandoned" is precisely the decision NOT to land it.
    if bool(item.get("done_at")) or str(item.get("status")) == "done":
        raise HTTPException(
            status_code=409,
            detail=f"this item is finished ({item.get('outcome') or 'done'}) — there is nothing "
                   f"left to decide. Its branch stays unmerged by that decision.")
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
