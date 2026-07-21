---
name: build
description: "Implement a build-phase work-item inside its git worktree: work the plan's task checklist, commit as you go, stay fresh with main. Use when a work-item is in its build phase and code is to be written; not for planning (use plan), verifying finished work (use vet), or research items (use investigate)."
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
and welcome — tidying into reviewable commits happens at review, not here. Never tick a box for
partially-done work; the progress bar the owner watches is derived from exactly these boxes.

## 3 — Stay fresh on long builds

When the build spans sessions or main is moving, run `sync_from_main` (commit first — it refuses a
dirty tree). Conflicts it reports are yours to resolve in the worktree: re-run the merge yourself
(`git merge <trunk>` via Bash), fix the markers, commit.

When a change makes an anchor doc (architecture/roadmap/capabilities/decisions) wrong or incomplete,
draft the fix NOW while the reasoning is hot: `stage_knowledge_delta(item_id, ops)`. Restage freely
as the build evolves — it stays a draft until the owner's merge applies it. Never edit the anchor
docs directly.

Some contract edits are **above your pay grade** — they DEFINE or alter intent, not just sync docs
to shipped reality (renaming/re-scoping a deliverable, a direction-setting decision, deleting a
doc, editing a retired doc). You cannot self-authorize those. **CALL `request_authorization`** now
(what · why · doc · scope · the vet check it blocks) — actually call the tool, don't just list it as
a task: the blocked check DEFERS and the request rides to review, where the owner or a delegated
deputy grants (routes back to you) or denies (accepts the gap). Then finish every OTHER task and
report `partial`. **Do NOT stop, and do NOT report that a human decision is missing** — deferring IS
how you hand the decision to the owner without pausing the loop; build never waits on a person
mid-loop. Your deferral is a **needs-you record** the vetter READS: it SKIPS that check (doesn't
re-judge a wall only the owner can clear) and carries it to review. So one clean `request_authorization`
converges the loop in a single cycle — whereas trying to force an owner-reserved change past vet just
churns it (every cycle fails the same check) until the budget breaker stops you. **Never work around a wall by editing the `## Vet plan` — that is the exam, authored by
plan/review; re-pointing your own checks to dodge a wall is exactly the self-grading the vetter is
there to catch.** Defer it; don't disguise it.

## 4 — Blockers and side-discoveries branch off, they don't derail

Something must be fixed before this item can proceed → `create_inbox_item` with your item as
`spawned_from_item` and relation `blocking` (auto-pushed; this item pauses). Worth doing but not
now → relation `spawn` (waits in the inbox). Never absorb out-of-scope work into this worktree —
it bloats the merge the owner must judge.

## 5 — Unknowns and walls become assumptions, never a stall

Build is an autonomous phase: the owner is not watching, and a question you raise here reaches them
only if they happen to look. So **do not stop to ask.** Decide, call `record_assumption` (what you
chose · why · what breaks if it's wrong), and keep going. It reaches them at the next gate as a
confirm/adjust card, and the close gate refuses while any assumption is unratified.

Record one when the plan didn't settle something and your choice would be **expensive to reverse
later** or **changes what the owner receives**. Don't record trivia you'd never expect them to
overturn — a ledger of twenty non-decisions buries the two that mattered.

A **wall is the same move.** If a task is walled off so you can't do it *yourself* — a tool can't
perform it, a policy forbids it, a doc is read-only, it needs a decision above your pay grade — do
**not** report `blocked` and stop. Record what you left undone as an assumption (what · why you
couldn't · your recommendation · the cost if it's wrong), complete every OTHER task, and report
`success` (all done) or `partial` (some done, gaps recorded). The wall then surfaces at **review**,
where the owner/deputy decides — accept it, or send it back with the authorization. `blocked` is
reserved for the rare case where **nothing was doable at all**, and even then you record the
assumption first. One thing you can't touch must never park the whole item mid-build.

## 6 — End of session

Bank a checkpoint (`write_checkpoint`): what you're on, decisions + tradeoffs made, what remains,
tried-but-failed. The next session cold-starts from it — write what the transcript loses, point at
artifacts by path. When ALL boxes are ticked, run the plan's **`## Behavior preview`** surface
yourself and compare against its predicted After — a mismatch is either your bug or a stale
prediction; fix the one that's wrong before declaring done. Then say the build looks complete and
stop — validation is the next phase, and the owner advances phases.

## Background runs

On a background run (a loop-driven build cycle — the kernel fired this turn; see the system
prompt), the trigger names your work order. It is one of three:

- **The plan** — the loop's OPENING cycle (build-first): nothing is implemented yet. Work
  `plan.md`'s `## Tasks` checklist in order, exactly as §1–2 above.
- **A failed vet report** — the loop's failure hop. Fix what its **findings** describe (the
  findings say what IS wrong; you decide how). Don't re-litigate a finding (the evidence ledger
  already recorded it) and don't expand scope beyond the failures plus what they force.
- **A routed review check + the owner's feedback** — a review re-entry. Meet the routed check.

Whichever it is, before you finish: run the plan's **`## Inner checks`** until every one exits
green — a cycle handed back with red inner checks burns a whole vet run to learn what an exit code
already said. Commit your work in the worktree; on a judgment fork, make the reasonable call and
record it in your commit message or checkpoint. The loop vets what you produce automatically;
never advance the phase.

## Pitfalls

- **Ticking boxes optimistically** — an unverified tick corrupts the one progress signal.
- **Riding through a stale plan** — divergence between plan and code makes every later gate brief
  wrong; amend the plan first.
