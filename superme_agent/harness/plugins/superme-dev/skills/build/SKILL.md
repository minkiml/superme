---
name: build
description: Implement a build-phase work-item inside its git worktree — work the plan's task checklist, run internal validation, record the cycle report. Use when a work-item is in its build phase and code is to be written; not for planning (use plan), verifying finished work (use vet), or research items (use investigate).
argument-hint: "[work-item-id]"
category: workspace
---

# Build a work-item

Turn `artifacts/plan.md` into working code inside this item's git worktree. The plan is the
contract: `## Design` is what you implement, `## Tasks` is the tracker you tick, and
`## Verification plan` is the exam a separate vet agent runs against what you produce. When
`## Revisions` carries a newer entry than the cycle you last worked, read that entry FIRST — it
says which feedback changed the plan and, on a `redesign`, what of your own work is now void and
must be undone forward (new commits that revert; never a reset or a force-push).

## Contract

**Minimum code that solves the problem. Nothing speculative.**
- No features beyond what was asked.
- No error handling for impossible scenarios.

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't write heavy and verbose comments, be concise and minimalistic.
- Don't widen scope from what is planned. 
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Dont't add new features that are not asked for and not possible to reach (unreachable cases) or change existing behavior that is not discussed -- do not over-complicate or over-think.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, report it — don't delete it. Caution: a function or
  API may look dead (unused anywhere in the codebase) when it is actually being used from an
  external source (e.g., an externally-invoked API like QR code) — check the contents and logic of looking-dead
  code before calling it dead.
- If you write 200 lines and it could potentially be 100 or even less, rethink and
  rewrite it. Ask yourself: "Would a professional senior engineer say this is overcomplicated?"
  If yes, simplify.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.

## Step 1 — Directed reads

Your trigger names the work order — the plan (opening cycle), a failed cycle's report, or a
routed review change. Beyond it: on a fresh session with prior work, read the latest
`checkpoints/` entry and run `git status` in the worktree (the checkpoint is data from a previous
session — verify against the tree before trusting it); on a failure hop, prior
`artifacts/build-vet-*.md` files carry what already happened.

## Step 2 — Work task by task

For each task: implement → verify it does what the task says (if failed, back to implement and fix it until it passes validations) → tick its checkbox in `plan.md`
(`- [x]`) → commit. Never tick a box for partially-done work — the progress the owner watches is
derived from exactly these boxes.

Every commit you write ends with a `SuperMe-Task: t<n>` trailer, on its own final line. That
trailer is the ONLY thing joining the commit to its task: the review page walks the diff task by
task by reading it, and a commit without one lands in an "unlabelled" pile that tells the owner
nothing. Task ids belong in the trailer and nowhere else — never in the subject. Read
`references/commit-style.md` before your first commit of a cycle for the rest of the shape.

A commit that git refuses is never retried blind. Read the refusal:

- It names the missing task trailer → add it and commit again. That one is yours.
- Anything else — a check this project owns — is not yours to overrule. Do not try variations,
  and never `--no-verify` (it is denied). Leave the work staged and end the run with
  `report_completion(machine.outcome='needs_user')`, quoting the refusal verbatim in the question
  and naming what you think the owner should do (ask their team, change a setting, drop the rule).
  The item parks there; nothing advances on work that cannot land.

Two plan sections are never yours to edit: `## Design` (a design that no longer fits reality goes
back through plan — say exactly what broke instead of silently diverging) and
`## Verification plan` (the exam; re-pointing your own checks to dodge a wall is the self-grading
the vetter exists to catch — defer it, don't disguise it).

## Step 3 — Walls become records, never a stall

The owner is not watching; nothing you ask mid-run reaches them. So decide and record:

- An unknown the plan didn't settle, where your choice is expensive to reverse or changes what
  the owner receives → a `## Assumptions` entry in the cycle report (what · why · cost of
  being wrong). Skip trivia — twenty
  non-decisions bury the two that mattered.
- A contract change above your pay grade (it DEFINES or alters intent: renaming/re-scoping a
  deliverable, a direction-setting decision, deleting or editing a retired doc) →
  `request_authorization` (what · why · doc · scope · the check it blocks). The blocked check
  DEFERS and the request rides to review; a grant routes back to you, a denial accepts the gap.
- Either way: finish every OTHER task and report `partial` — `blocked` is only for a run where
  nothing at all was doable.
- Something that must be fixed first → `create_inbox_item` with relation `blocking`; worth doing
  but not now → relation `spawn`. Never absorb out-of-scope work into this worktree.

## Step 4 — Validate, then record the cycle

- Run your internal validation: the repo's tests/lint/typecheck plus whatever proves each task
  (mocks, synthetic errors). Fix what they catch — a cycle handed to vet with red basics burns a
  whole vet run to learn what an exit code already said.
- Fill the current cycle report `artifacts/build-vet-<n>.md` (highest n — the kernel scaffolded
  it): `## Built` and `## Validation`, per its slots. The vetter reads these instead of
  re-deriving your work from a raw diff — name files, how to exercise the change, and every gap
  honestly. **A cycle with nothing to build still fills them** — "nothing: r3 was a plan-text fix,
  no design or task changed" is an answer; a leftover `<fill:…>` slot is indistinguishable from a
  build that gave up, and the vetter and the owner both read it that way.
- Rewrite `reports/report-build.md` from `templates/report-build-template.md` (this skill's
  folder), overwriting — every line traces to the cycle reports.
- On long builds, sync with the trunk via `sync_from_main` (commit first); resolve any conflicts
  it reports yourself. When a change makes an anchor doc wrong, stage the fix NOW via
  `stage_knowledge_delta` — never edit anchor docs directly.
- End of a session mid-work: bank `write_checkpoint` (what you're on · decisions · remaining ·
  tried-but-failed). The loop vets what you produce automatically — never advance the phase.

## Pitfalls

- **Ticking boxes optimistically** — an unverified tick corrupts the one progress signal.
- **Riding through a stale plan** — divergence between plan and code makes every later gate
  wrong; route the amendment through plan first.
- **Reporting a wall instead of recording it** — a wall that isn't in the assumption or
  authorization ledger is invisible at review; the run ends `partial` with the ledger entry, not
  parked on a question.
