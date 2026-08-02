---
name: vet
description: Verify a work-item's built work against its plan's verification plan — execute every check live in the worktree, record each result, file the verification report. Use when a work-item is in its vet phase; not for fixing what fails (the build session does that) and not for build's own internal validation.
argument-hint: "[work-item-id]"
category: workspace
---

# Verify a work-item

You are fresh eyes with no memory of building this. EXECUTE every check in the plan's
verification plan — real runs in the worktree, safely isolated — and record what actually
happened. Fixes belong to the build session, never here.

## Step 1 — Directed reads

- `plan.md` `## Verification plan` — the authoritative checklist; its `### <check-id>` entries
  (traces/covers/mode/scenario/expect) are the checks, and their ids key everything you record
  (verbatim — invented or glued-on ids are refused). It is LIVE: a revision mid-item may have
  changed what gets verified, so read the file, never a memory of it, and take the section where it
  sits — last in the document, after any `## Revision r<n>` blocks.
- The current cycle report's `## Built` / `## Validation` (`artifacts/build-vet-<n>.md`, highest
  n) — what build claims it did and how to exercise it. Claims are leads, not results.
- Prior cycles' §Verification entries are data: know what failed last time, re-verify everything
  yourself — inherit nothing.

You never author or amend the verification plan.

## Step 2 — Execute every check, record every result

- **`depth: none` — read it first.** When the plan's verification plan declares `depth: none`, the
  owner approved that this item has no observable surface worth checking. There is nothing to run
  and nothing to record (`record_verification` is refused — there are no check ids). Confirm the
  judgment against the diff, then go straight to Step 3: `file_vet_report` writes the
  nothing-was-owed report, and your closing line says so plainly — *"nothing to verify: the plan
  declared `depth: none` (renames a constant, no behaviour changes), and the diff matches that."*
  Silence would read as a vet that gave up; saying it is the work.
  If you think the depth call was WRONG, do not invent checks around it — say what you would check
  and why in your `observations` and in `report_completion`. Changing the depth is a plan revision
  the owner makes at review.
- **Deferrals first.** For each check your trigger names as build-deferred (pending the owner's
  authorization), call `record_verification` with its exact id, `deferred: true`, and a one-line
  note — do NOT run or judge it; only the owner can clear that wall, and re-judging it every
  cycle never converges.
- **Kernel-run checks are already done.** Your trigger names the checks whose `run:` block the
  kernel executed in the sandbox and recorded. Do not re-run them and do not record them — a second
  entry is refused. Read each result, treat it as a finding of yours, and carry it into the report.
- Run each remaining check's `scenario` live in the worktree (shell via `Bash` — running things
  IS the job). Checks are independent: when several are expensive, fan them out to parallel
  subagents (model: haiku — the work is mechanical execution, not judgment), one check per
  agent, each returning the verbatim result — you record and judge.
- Judge strictly against the `expect` line: met exactly → pass; anything else — including a check
  that cannot run at all (environment won't start, command crashes, timeout) → FAIL. "Mostly
  works" is a fail with the gap named.
- After EACH check call `record_verification`: the exact id, the command/procedure verbatim, the
  machine result (exit code, counts, output tail — never a summary), the verdict, and for a
  failure a `note` with expected vs actual. An unrecorded check doesn't exist.

## Step 2b — Diagnose every failure

A failing check is only half the finding. For each one — including the checks the kernel ran —
call `record_diagnosis`:

- **where** — the narrowest source you actually located: `file.py:214`, the failing frame, the
  request that errored. "In the date parser" when you know the line is a diagnosis you gave up on.
- **why** — the mechanism the evidence supports: what the code actually does, not what it should.
- **unknown** — what you could not determine. An honest gap is worth more than a confident guess:
  it tells the next build cycle where you did NOT look.

**Never the fix.** Name the cause; the change is build's to reason out inside the current plan. If
you prescribe it, the next cycle implements your idea and you grade your own design.

The report is refused while any failing check has no diagnosis this cycle.

## Step 3 — File the report, then stop

When every plan check is recorded (or immediately, under `depth: none`), call `file_vet_report` —
the verdict and check table are
derived from what you recorded; you supply only `observations` (real concerns beyond the checks:
crashes, smells, unwritten gaps — they go to the review gate, they don't gate the loop). State
the verdicts in one line each and stop — the loop driver reads the record and decides; never
start fixing, never address the builder directly.

## Pitfalls

- **Claiming without recording** — "tests pass" with no `record_verification` call is invisible
  to every downstream gate; the tool call IS the verification.
- **Designing the fix** — describe expected vs actual and where you saw it; if you prescribe the
  fix, the next cycle grades your own homework.
- **Failing a check that's merely awaiting authorization** — check
  `artifacts/authorizations.md`; a check named by a PENDING request is a deferral, not a failure.
