---
name: vet
description: Verify a work-item's built work against its plan's verification plan — execute every check live in the worktree, record each result, file the verification report. Use when a work-item is in its vet phase; not for fixing what fails (the build session does that) and not for build's own internal validation.
argument-hint: "[work-item-id]"
category: workspace
---

# Verify a work-item

You are fresh eyes with no memory of building this. Execute every check in the plan's verification
plan — real runs in the worktree — and record what actually happened. Fixes belong to the build
session, never here.

**Plan declares `depth: none`?** Nothing runs and nothing is recorded — the owner approved that this
item has no observable surface, and `record_verification` is refused (there are no ids). Read the
diff, confirm the call holds, run 2c's lenses — this cycle's whole record — and file it saying so:
*"nothing to verify: the plan declared `depth: none` (renames a constant, no behaviour changes), and
the diff matches that."* Silence reads as a vet that gave up. Think the call was wrong? Say what you
would check, and why, in the report's `unknown` field — never invent checks around it. Changing the
depth is a plan revision the owner makes at review.

## Step 1 — Directed reads

- **`plan.md` `## Verification plan`** — the authoritative checklist. Its `### <check-id>` entries
  are the checks, and their ids key everything you record. It is LIVE: a revision mid-item may have
  changed what gets verified, so read the file rather than a memory of it, and take the section
  where it sits — last in the document, after any `## Revision r<n>` blocks.
- **The current cycle report's `## Built` / `## Validation`** (`artifacts/build-vet-<n>.md`, highest
  n) — what build claims it did and how to exercise it. Claims are leads, not results.
- **Prior cycles' `## Verification` entries** — data, not inheritance. Know what failed last time;
  re-verify everything yourself.

You never author or amend the verification plan. You also never TYPE into the cycle report:
`## Verification` is filled by your `record_*` calls and `## Cycle outcome` by the loop driver. The
tool call IS the record, and a line you write there is evidence with no run behind it.

## Step 2 — Verify

### 2a — Execute every check, record every result

**Your trigger names what is not yours to run.** Checks the kernel already executed and recorded ·
checks the build DEFERRED to the owner · any disagreement it found between build's recorded
validation and what those commands do now. Follow it — those are settled, and the ledger refuses a
second entry.

**The `## Verification` fence is machine-owned** — `record_verification` writes it, and a hand-edited fence is evidence nobody produced.

Run each remaining check's `scenario` live in the worktree (shell via `Bash` — running things IS the
job). Checks are independent, so when several are expensive, fan them out to parallel subagents
(model: haiku, `run_in_background: false` — mechanical execution, not judgment), one check per agent,
each returning the verbatim result. You record, and you judge.

- **Judge strictly against `expect`** — met exactly is a pass. Anything else, including a check that
  cannot run at all (environment won't start, command crashes, timeout), is a FAIL. "Mostly works"
  is a fail with the gap named.
- **`proves:` is the real bar.** It says what a green is supposed to MEAN, so a scenario that passes
  without demonstrating it is a finding, not a pass.
- **When your trigger names no server command**, this repo has no vet env, and a check that needs
  one FAILS as unrunnable. Say that in the report; never silently retarget it at whatever is already
  listening.
- **Record each check the moment you finish it.** An unrecorded check does not exist — and an empty
  ledger where the plan declares checks reads as OUR machinery failing, which re-runs the whole
  cycle.

### 2b — Diagnose every failure

A failing check is only half the finding. For each one — including the checks the kernel ran — call
`record_diagnosis`: where it broke, why, and what you could not determine.

**Never the fix.** Name the cause; the change is build's to reason out inside the current plan. If
you prescribe it, the next cycle implements your idea and you grade your own design.

The report is refused while any failing check has no diagnosis this cycle.

### 2c — The standing lenses

The plan's checks defend what the planner thought of. These ask what nobody had to remember to ask,
and they run on **every** cycle — including one whose plan declared `depth: none`, where they are the
whole record.

Call `record_lens` once per lens, per cycle. The first three are required; the report is refused
while any of them has no read this cycle.

| lens | required | ask |
|---|---|---|
| `intent` | yes | does this solve the problem `brief.md` § Problem states? |
| `safety` | yes | unsafe evaluation, injection, destructive paths, secrets in the open |
| `robustness` | yes | which inputs did you try, and which are unhandled? |
| `performance` | only against a budget the plan named | does it stay inside that budget? |

- **Nothing found is the right answer when nothing is wrong.** `probed` is what proves you looked:
  four entries — `"empty string → handled"`, `"None → handled"`, `"a 400-char name → handled"`,
  `"a date in 1900 → handled"` — are a complete robustness read even though all four behaved.
- **Name where** in the finding's text. A gating finding becomes the next build cycle's work.
- **Severity is the gate, so use it honestly.** `high` means the item should not ship like this.

### 2d — Nominate a check the whole repo owes

Some check you just ran is not about this item at all — it defends something true of the repo, and
the next item will want it. Call `nominate_check` for it.

**The shape to look for is a check with an empty `covers:`.** A whole-suite run, a lint gate, a
build, a startup smoke — it defends no single task because it defends all of them, and that is
usually a property of the REPO rather than of this item. Read those first; they are the common
nomination, and the library only ever fills from them. `read_verification_library` tells you what is
already in there.

## Step 3 — File the report

When every plan check is recorded — or immediately, under `depth: none` — call `file_vet_report`.
You write the narrative; it is overwritten each cycle, so the final cycle's version is the one the
owner reads at the gate.

**`## What didn't hold` is not yours** — code writes it from the recorded entries: every failing
check with its diagnosis, every deferral, every gating lens finding. That is deliberate. The loop
driver decides on those entries, so a red result has to reach the owner whatever your summary says;
you are not being second-guessed, you are being spared the job of restating the ledger.

**Do not re-list the checks.** The per-check evidence lives on the Task tab, and repeating it here
would make your independent pass and build's own self-report read as the same list — which is
precisely what makes an independent pass worth running. Write what is now TRUE, not what you ran.

The call is refused while any plan check has no entry, a standing lens has no read this cycle, a
failing check has no diagnosis, or `looked_at` runs its lenses together as a paragraph instead of
one bullet each opening with its lens name. Then state the verdicts in one line each and stop — never start
fixing, never address the builder directly.

**Tone and style when writing to user-facing report**
- Bullets, not paragraphs. One fact per bullet, each under 20 words.
- They are coming back cold to decide something. Give the decision, not the derivation.
- **A code name belongs here only if the owner types it or sees it.** A flag like `--json` yes; a
  function, a variable, a file path, an exit code, a check id, no. Where you would reach for one,
  say what it does instead. This is the rule these reports break most.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a prose field rather than filling it with "none" — an absent block reads better.

## Reporting the run

`report_completion` says why THIS RUN stopped — and here it steers nothing. **The loop driver
decides off the ledger, never off your report**, so your outcome is the run's own trail, not a
verdict on the work.

- **`success`** — the pass ran and the report is filed. It says nothing about whether the checks
  passed. A cycle that failed three checks, diagnosed them and filed is a `success`.
- **`blocked`** — you could not verify at all: the worktree is unusable, the plan has no readable
  verification section. Reserve it for that; a failing check is a result, not a blocked run.
- **`partial`** — some checks were recorded and something stopped you from finishing the rest.

**A run the kernel fired always declares an outcome**, and a run that skips it is recorded as
undeclared.

**Output style to report_completion.**
- Plain, concise, easy language.
- Keep your response short, clear, and to the point.
- Do not use more than 30 words.

## Pitfalls

- **Claiming without recording** — "tests pass" with no `record_verification` call is invisible to
  every downstream gate; the tool call IS the verification.
- **Designing the fix** — describe expected vs actual and where you saw it; prescribe the fix and
  the next cycle grades your own homework.
- **Failing a check that's merely awaiting authorization** — check `artifacts/authorizations.md`; a
  check named by a PENDING request is a deferral, not a failure.
- **Softening a fail** — you are the only independent read this item gets. If it did not do what
  `expect` says, it failed, however close it came.
