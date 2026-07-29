---
name: review
description: Write the report the owner decides on at a work-item's review gate, and stage any anchor-doc update the work earned. Use when a work-item enters its review phase; not for running the checks (vet does that), not for changing the plan (only a revise routes it back), not for merging (the owner's gate).
argument-hint: "[work-item-id]"
category: workspace
---

# Present a work-item for review

This run fires the moment the item enters review, before anyone has looked at it. The work segment
is DATA — read it, never re-run it. Approving what you write locks this item's work in, so a claim
the artifacts don't carry is the one failure this phase cannot recover from.

**Read-only on the tree.** Inspect git freely (shell via `Bash`), change nothing: no sync, no
commit, no merge. Your only writes are `reports/report-review.md` and the staged knowledge delta.

## Step 1 — Directed reads

- `artifacts/plan.md` — the contract this work was measured against.
- **Implementation:** every `artifacts/build-vet-<n>.md`, oldest to newest — what each cycle built,
  what vet found, how it ended. The ARC across them is what the report carries.
- **Research:** `artifacts/investigation.md` and `reports/report-investigate.md`.
- `artifacts/authorizations.md` if present, and `checkpoints/` (newest first).

Numbers come from their source, never an estimate: the diff shape from one
`git diff --stat <base>...HEAD` run in the worktree — `<base>` is the **branch base** named in
your `## Current focus` block — and check results from the cycles' `§Verification`.

## Step 2 — Name what the anchor docs will owe

Did this item change something the anchor docs describe (project-prd / architecture / capabilities /
decisions / roadmap / resources)? Say so in the report, doc by doc, in one line each — the CLOSE run
writes them, after the merge locks the code, and it reads this. Nothing doc-worthy changed → say
that too; silence reads as an oversight.

An op that DEFINES or ALTERS intent is not a sync — re-scoping a deliverable, changing a written
success signal, setting direction. Those go through `request_authorization`, naming the ops they
cover.

**Example — two changes on one item, two different calls:**

| the change | the call |
|---|---|
| `architecture.md` — record that the CSV writer moved into `reporting/` | name it here; close writes it — it describes what now exists |
| `project-prd.md` — drop `--csv` from deliverable `d-reporting` and rewrite its success signal | `request_authorization` — it changes what the project promised |

The tell is not which doc you touch — it is whether the line you change DEFINES what was promised.

**A research item skips this step:** nothing it concluded has been implemented, so no anchor doc
owes it anything. Its proposals in Step 3 are how its findings become work.

## Step 3 — Write the report

Copy your kind's template from this skill's `templates/` folder to `reports/report-review.md` and
fill it:

| kind | template | the question it answers |
|---|---|---|
| implementation | `report-review-template.md` | does this change hold up — what changed · evidence · risk |
| research | `report-review-research-template.md` | what did we learn, and what work should now exist |

Lead with what the owner must decide. Every line traces to a doc from Step 1 — no new facts — and
an empty risks section must be TRUE, not optimistic.

`reports/report-review.md` already exists ⇒ this is a re-write after a `revise`: overwrite in
place and fill `## Changed since`. On a first write, delete that section.

Research only: `## Proposed work` is the second deliverable. Each proposal is independently
startable (two that cannot begin in either order are ONE proposal, and its plan phase splits it),
checked against the live board so a duplicate is named as one, and never created here. "Nothing
should change" is a finished investigation, not a failed one.

## Step 4 — Report, and name the commit

`report_completion`, stating in one line what is being put to the owner and what is still open.

On an implementation item the same call carries `machine.commit` — how this work should read in the
project's permanent history once it lands. You are the last phase that knows what actually shipped, so you
choose it and the kernel writes it. Pick the type by the behaviour, not by the nearest-sounding
word: **gained** something → `feat` · was **corrected** → `fix` · **unchanged** → `refactor` · not
product code at all (dependencies, build, tooling) → `chore`. A research item declares none —
nothing lands.

## Pitfalls

- **A claim the artifacts don't carry** — "tests pass" either cites the check and its evidence row,
  or says plainly that it is unverified.
- **Restating the plan** — the owner can open plan.md. What they cannot reconstruct is the arc:
  what changed across cycles, what it cost, what is still open.
- **Fixing anything** — no code, no plan edit, here or later in this session. If the work must
  change, the owner routes it back.
