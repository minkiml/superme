---
name: build
description: "Implement a build-phase work-item inside its git worktree: work the plan's task checklist, commit as you go, stay fresh with main. Use when a work-item is in its build phase and code is to be written; not for planning (use plan), verifying finished work (use validate), or research items (use investigate)."
argument-hint: "work-item id (optional — defaults to the bound item)"
category: workspace
---

# Build a work-item

Turn the approved `plan.md` into working code, inside this item's dedicated git worktree (your
working directory — writes outside it are denied). The plan is the contract; the `## Tasks`
checklist is the tracker.

## 1 — Orient on the plan

Read `artifacts/plan.md`. Unchecked tasks are your queue, in order unless dependencies say
otherwise. If the plan no longer fits reality, say exactly what broke and propose the amendment —
update `plan.md` with the owner's agreement rather than silently diverging from the approved plan.

## 2 — Work task by task

For each task: implement → verify it does what the task says → tick its checkbox in `plan.md`
(`- [x]`) → commit in the worktree with a message naming the task. Checkpoint commits are cheap
and welcome — tidying into reviewable commits happens at deliver, not here. Never tick a box for
partially-done work; the progress bar the owner watches is derived from exactly these boxes.

## 3 — Stay fresh on long builds

When the build spans sessions or main is moving, run `sync_from_main` (commit first — it refuses a
dirty tree). Conflicts it reports are yours to resolve in the worktree: re-run the merge yourself
(`git merge <trunk>` via Bash), fix the markers, commit.

When a change makes an anchor doc (architecture/spec/roadmap) wrong or incomplete, draft the fix
NOW while the reasoning is hot: `stage_knowledge_delta(item_id, ops)`. Restage freely as the build
evolves — it stays a draft until the owner's merge applies it. Never edit the anchor docs directly.

## 4 — Blockers and side-discoveries branch off, they don't derail

Something must be fixed before this item can proceed → `create_inbox_item` with your item as
`spawned_from_item` and relation `blocking` (auto-pushed; this item pauses). Worth doing but not
now → relation `spawn` (waits in the inbox). Never absorb out-of-scope work into this worktree —
it bloats the merge the owner must judge.

## 5 — End of session

Bank a checkpoint (`write_checkpoint`): what you're on, decisions + tradeoffs made, what remains,
tried-but-failed. The next session cold-starts from it — write what the transcript loses, point at
artifacts by path. When ALL boxes are ticked, say the build looks complete and stop — validation
is the next phase, and the owner advances phases.

## Pitfalls

- **Working outside the worktree** — the main tree is the owner's; everything you change lives on
  this item's branch until the deliver gate merges it.
- **Ticking boxes optimistically** — an unverified tick corrupts the one progress signal.
- **Riding through a stale plan** — divergence between plan and code makes every later gate brief
  wrong; amend the plan first.
