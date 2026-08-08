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
  (proves/traces/covers/mode/scenario/expect) are the checks, and their ids key everything you
  record (verbatim — invented or glued-on ids are refused). Read `proves:` before you run
  anything: it says what a green is supposed to MEAN, and a scenario that passes without
  demonstrating it is a finding, not a pass. It is LIVE: a revision mid-item may have
  changed what gets verified, so read the file, never a memory of it, and take the section where it
  sits — last in the document, after any `## Revision r<n>` blocks.
- The current cycle report's `## Built` / `## Validation` (`artifacts/build-vet-<n>.md`, highest
  n) — what build claims it did and how to exercise it. Claims are leads, not results.
- Prior cycles' §Verification entries are data: know what failed last time, re-verify everything
  yourself — inherit nothing.

You never author or amend the verification plan.

You also never TYPE into the cycle report. `## Verification` is filled by your `record_*` calls and
`## Cycle outcome` by the loop driver; both are machine-owned, and a line you write there is
evidence with no run behind it. The tool call IS the record.

## Step 2 — Execute every check, record every result

- **`depth: none` — read it first.** When the plan's verification plan declares `depth: none`, the
  owner approved that this item has no observable surface worth checking. There is nothing to RUN,
  and no check to record against (`record_verification` is refused — there are no check ids). The
  lenses in step 2c still run: depth governs execution, not whether the work is read, and they are
  this cycle's whole record. Confirm the judgment against the diff, then go to Step 3:
  `file_vet_report` writes the
  nothing-was-owed report, and your closing line says so plainly — *"nothing to verify: the plan
  declared `depth: none` (renames a constant, no behaviour changes), and the diff matches that."*
  Silence would read as a vet that gave up; saying it is the work.
  If you think the depth call was WRONG, do not invent checks around it — say what you would check
  and why in the report's `unknown` field and in `report_completion`. Changing the depth is a plan
  revision the owner makes at review.
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
- **A check with a `rubric` is judged criterion by criterion.** Pass every one of the plan's
  criteria to `record_verification` in `met` or `missed`, verbatim — none left out, because a
  criterion you skip reads as one you judged. **Any missed criterion means the check FAILED**: a
  rubric is the bar, not a score. Both rules are enforced, so a partial record is refused rather
  than quietly recorded.
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

## Step 2c — The three standing lenses

The plan's checks defend what the planner thought of. These ask what nobody had to remember to
ask, and they run on **every** cycle — including one whose plan declared `depth: none`, where they
are the whole record. Call `record_lens` once per lens with what you **probed** — a LIST, one probe
per entry (an input you tried, a path you read, a command you ran, each with its outcome). Never one
paragraph: the owner reads this list to count what was actually checked. Findings only if there are
any.

| lens | ask | a finding here |
|---|---|---|
| `intent` | does this solve the problem `brief.md` § Problem states? | gates — back to build |
| `safety` | unsafe evaluation, injection, destructive paths, secrets in the open | gates |
| `robustness` | which inputs did you try, and which are unhandled? | `high` gates; below that it rides to review |
| `performance` | only against a budget the plan actually named | never gates |

- **Nothing found is the right answer when nothing is wrong.** Never manufacture a finding to look
  thorough — `probed` is what proves you looked. Four entries — `"empty string → handled"`,
  `"None → handled"`, `"a 400-char name → handled"`, `"a date in 1900 → handled"` — are a complete
  robustness read even though all four behaved.
- Name **where** in the finding's text. A gating finding becomes the next build cycle's work.
- Severity is the gate, so use it honestly: `high` means the item should not ship like this.

## Step 2d — Nominate a check the whole repo owes

Some check you just ran is not about this item at all — it defends something true of the repo, and
the next item will want it. Call `nominate_check` for it, with `general` saying what it defends
**about the repo**, without mentioning this item. If you cannot say it that way, it is not a
library entry.

**The shape to look for is a check with an empty `covers:`.** A whole-suite run, a lint gate, a
build, a startup smoke — it defends no single task because it defends all of them, and that is
usually a property of the REPO rather than of this item. Read those first; they are the common
nomination, and the library only ever fills from them.

- Only a check that has **passed** here. An untested entry costs the next item a cycle.
- Already in the library? Don't nominate it again — `read_verification_library` tells you.
- You nominate; **close writes it in** and the owner decides whether it becomes standing. Nothing
  you do here changes `general/`.

## Step 3 — File the report

When every plan check is recorded (or immediately, under `depth: none`), call `file_vet_report`.
**You write this report** — four fields, in the owner's words:

| field | what goes in it |
|---|---|
| `summary` | one line: what this pass establishes. The dashboard shows it alone. |
| `confirms` | `## What this confirms` — a bullet per thing now known to be true |
| `looked_at` | `## What else was looked at` — the lenses, as questions you asked and what came of them |
| `unknown` | `## What I can't tell you` — one line and a short reason. Omit only when there is genuinely nothing |

**`## What didn't hold` is not yours** — code writes it from the recorded entries: every failing
check with its diagnosis, every deferral, every gating lens finding. That is deliberate. The loop
driver decides on those entries, so a red result has to reach the owner whatever your Summary says;
you are not being second-guessed, you are being spared the job of restating the ledger.

**Do not re-list the checks.** The per-check evidence lives on the Task tab, and repeating it here
would make your independent pass and build's own self-report read as the same list — which is
precisely what makes an independent pass worth running. Write what is now TRUE, not what you ran.

The call is refused while any plan check has no entry, a standing lens has no read this cycle, or a
failing check has no diagnosis. Then state the verdicts in one line each and stop — the loop driver
reads the record and decides; never start fixing, never address the builder directly.

**Tone and style when writing to report-vet doc only**
- Plain, easy language. Fewer words wins.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a prose field rather than filling it with "none" — an absent block reads better.

## Chat response style
- Use plain and easy language.
- Keep your response short, clear, and to the point.
- Use bullets or numbered lists to organize information if there is more than one point.

## Pitfalls

- **Claiming without recording** — "tests pass" with no `record_verification` call is invisible
  to every downstream gate; the tool call IS the verification.
- **Designing the fix** — describe expected vs actual and where you saw it; if you prescribe the
  fix, the next cycle grades your own homework.
- **Failing a check that's merely awaiting authorization** — check
  `artifacts/authorizations.md`; a check named by a PENDING request is a deferral, not a failure.
- when writing to docs, Do not include the comments part `<!-- ... -->` in the scaffold you file — it is instructions for you.