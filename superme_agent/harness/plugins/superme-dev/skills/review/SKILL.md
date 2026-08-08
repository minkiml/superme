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
commit, no merge. Your only writes are `artifacts/review.md`, `reports/report-review.md`, and the
staged knowledge delta.

You write TWO documents, in this order and for two different readers. `artifacts/review.md` is the
record — what changed, what is settled, what is proven, what still risks — and its readers are
machines and later agents: close, the landing commit, a revision cycle and its vetter. The report
is the owner's, and it is a judgment. Write the record first: the report is easy once the facts are
laid out, and impossible to keep honest when they aren't.

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

## Step 3 — Write the record

`scaffold_artifact(item_id, "review")`, then fill the slots. You get your kind's shape:
implementation records what landed; research records what was established and what work should
follow. Four things it must get right, because each has a named downstream reader:

- **`**Delivered:**`** — the kernel writes this line into the landing commit. It outlives this
  workspace by years, so write it as the project's history should read: what the code now does.
- **`## Settled — do not re-open in a revision cycle`** — every question this item closed, with who
  decided and when. A revision cycle reads this to know what is NOT back on the table. Omit one and
  the next round re-litigates it for free.
- **`## Proven vs taken on trust`** — cite into the cycles' `§Verification`, never re-transcribe it;
  the ledger is that table's one writer. What you add is the row the ledger structurally cannot
  hold: **the claim nothing covers**. Omitting it is the one failure this phase cannot recover from.
- **`## Revision rounds`** — append-only. On a first review it stays `_None. First review._`; on a
  re-write after a `revise` you APPEND one block and rewrite nothing above it. A superseded settled
  decision is marked superseded where it stands, never deleted — the record is how it was decided,
  not how it ended up.

**Research only:** `## Proposed work` is the second deliverable. Each proposal is independently
startable (two that cannot begin in either order are ONE proposal, and its plan phase splits it),
checked against the live board so a duplicate is named as one, and never created here. "Nothing
should change" is a finished investigation, not a failed one. `itemize` fills the decision line
after the gate — leave it alone.

## Step 4 — Write the report

Copy `templates/report-review-template.md` to `reports/report-review.md` and fill it. ONE template
for every kind — the four sections are deliberately kind-neutral, and an implementation item, a
research item and whatever kind comes later all answer the same four questions:

| section | implementation | research |
|---|---|---|
| What you're approving | the change, as an outcome | the answer, and what it rests on |
| What to push back on | the calls that could have gone the other way | the judgement calls in the method |
| How much to trust it | proven vs taken on trust | measured vs reasoned |
| Where this leaves the project | what pattern it sets, what it left behind | what the answer opens up |

`**Summary:**` is one line and the dashboard shows it alone at the gate, so it must stand without
the report around it. There is no verdict word and no recommendation line: the Summary says it and
the buttons do it.

Lead with what the owner must decide. Every line traces to `artifacts/review.md` or a doc from
Step 1 — no new facts — and `How much to trust it` carries the **not covered** rows too. A table of
only green rows is an advertisement, not a basis for a decision.

**A template's `<fill:…>` slots and `<!-- … -->` notes are instructions TO YOU. None of them belong
in the file you write** — you replace the slots and drop the notes. Copied through, an authoring
note becomes the report's opening paragraph, and the owner reads your instructions instead of your
review.

`reports/report-review.md` already exists ⇒ this is a re-write after a `revise`: overwrite it whole
and fill `## What you asked for` at the TOP, quoting their objection and saying what the item now
does about it and what that cost. Delete that section on a first review. This is the opposite of
Step 3's file, and deliberately: the report is always the CURRENT state of the item, because
someone deciding now should not have to read the history of the decision to find it — the record
keeps that history.

**Note**
- Do not include the comments part `<!-- ... -->` in the scaffold you file — it is instructions for you.

**Tone and style when writing to report-review doc only**
- Plain, easy language. Fewer words wins.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a prose field rather than filling it with "none" — an absent block reads better.

## Chat response style
- Use plain and easy language.
- Keep your response short, clear, and to the point.
- Use bullets or numbered lists to organize information if there is more than one point.

## Step 5 — Report, and name the commit

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
- when writing to docs, Do not include the comments part `<!-- ... -->` in the scaffold you file — it is instructions for you.