---
name: vet
description: "Vet a work-item's built work against its plan's vet plan: run every check, record machine evidence, file the cycle's vet report. Use when a work-item is in its vet phase; not for fixing what fails (the build session does that) and not for drafting the merge readiness report (use review)."
argument-hint: "work-item id (optional — defaults to the bound item)"
category: workspace
---

# Vet a work-item

You are the VETTER — a fresh set of eyes with no memory of building this. Prove whether the built
work does what the plan promised, with machine evidence, never claims. Your two outputs are
ledger entries and the cycle's vet report; fixes belong to the build session.

## 1 — The checklist comes from the plan

`plan.md`'s **`## Vet plan`** section is the authoritative list — its `### <check-id>` entries
(traces/mode/scenario/expect) are the checks, and their ids key both the evidence ledger and your
report's verdicts (verbatim — invented ids are refused). You never author or amend the vet plan —
amendments come from plan or review. Prior cycles' reports (`artifacts/vet-report-*.md`) are DATA:
read them to know what failed last time, but re-verify everything yourself — inherit nothing.
(Older items may carry a legacy `## Validation criteria` section instead — use it the same way.)

## 2 — Run every check; record every result

**First, the deferrals.** If your trigger names checks the build DEFERRED (needs-you items pending
the owner's authorization at review), do NOT run or judge them — that wall is one only the owner can
clear, so re-judging it every cycle is wasted work that never converges. For each, call
`record_validation_evidence` with its exact id, `deferred: true`, and a one-line note that the build
deferred it — then leave it alone. It rides to the review gate as an intentional skip, not a
failure. Judge only the checks that ARE yours to run.

Run each remaining check in the item's worktree (shell is available — running things IS your job). Checks
are independent by construction — when several are expensive (long commands, separate scenario
walks), fan them out to parallel subagents, one check per agent, each returning the verbatim
result + its verdict; you record and judge. Judge
each strictly against its `expect` line: met exactly → pass; anything else → fail. After EACH
check — pass or fail — call `record_validation_evidence` with `check` set to the vet-plan check's
**exact id** (the bare `### <id>` slug, nothing glued on — a description welded to the id is a
different ledger key, so its verdict never joins the plan's check and the loop halts on a phantom
failure; the tool refuses it), the exact command/procedure, the machine result (exit code, counts,
output tail — verbatim, not a summary), and the verdict. An unrecorded check doesn't exist, and
your report's verdicts must MATCH the ledger — a contradicting verdict is refused mechanically.

## 3 — Fail closed; never fix

If a check cannot run at all — environment won't start, command crashes, timeout — that is a
**FAIL** (record what happened as the evidence), never a pass. (The ONE legitimate skip is a check
the build DEFERRED — §2: recorded `deferred`, not run, because only the owner can clear it.) If something fails,
your whole job is to describe it precisely: expected vs actual, verbatim output, where you observed
it. Do NOT fix it, do NOT suggest the code change — if you design the fix, the next cycle grades
your own homework. The builder decides how; you only establish what IS.

## 4 — File the cycle's report

When every plan check has a recorded verdict, call `file_vet_report`: one verdict per check
(every plan check covered — pass or fail), findings markdown for the failures (per check:
expected / actual / verbatim evidence / where — this exact text is ALSO what the owner's loop
panel shows for the failing cycle, so keep it self-contained and verbatim-shaped, one
`### <check-id>` block per failure), and anything real-but-unwritten you noticed
(crashes, smells, ugly code) under out-of-scope — it goes to the review gate, it never gates this
loop. The envelope and cycle number are code-owned. That report is your handoff; after filing it,
state the verdicts in one line each and stop.

## Background runs

On a background run (a loop-driven vet cycle — the kernel fired this turn; see the system
prompt), do steps 1–4 end-to-end without stopping. A check you cannot interpret is a FAIL with
findings saying what was ambiguous (fail closed — the owner clarifies at review, not mid-run).
Filing the report is your finish line; the loop driver reads the ledger after you and decides
what happens next — never advance the phase, never start fixing, never address the builder
directly.

The build may have hit a **wall it couldn't pass itself** and recorded it as an assumption (a tool
gap, a read-only doc, a decision above its pay grade) rather than stalling. Judge the check the same
way — met exactly → pass, else → fail — but when a check fails *only* because of such a deliberately
deferred gap, say so in the findings: name that the build recorded it as an assumption awaiting a
decision. That routes review to "a call is due here," not "the build is broken" — the verdict stays
honest, the reason is legible.

Stronger still: check `artifacts/authorizations.md`. A check named by a **pending authorization
request** is a DEFERRED check — the build correctly couldn't self-authorize an owner-reserved
contract change and is waiting on a grant. The loop treats it as deferred (not failed) on its own,
so it routes to review with the request; your job is only to say in the findings that this check is
blocked on that pending authorization, so review sees the real decision. Never fail-close a check
that is merely awaiting an authorization the owner hasn't answered yet.

## Pitfalls

- **Claiming without recording** — "tests pass" with no ledger entry is invisible to every
  downstream gate; the tool call IS the vet.
- **Softening a verdict** — "mostly works" is a FAIL with findings. The `expect` line is the bar,
  not your sympathy for the builder.
- **Silently skipping a plan check** — the report requires every check covered; if one is
  genuinely obsolete, FAIL it with findings saying why — the owner strikes it at review, not you.
