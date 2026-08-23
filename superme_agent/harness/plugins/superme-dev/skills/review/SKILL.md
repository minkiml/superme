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

**You write TWO documents, in this order, for two different readers.**

| | `artifacts/review.md` | `reports/report-review.md` |
|---|---|---|
| what it is | the record — what changed, what is settled, what is proven, what still risks | a judgment — what the owner is deciding, and how much to trust it |
| who reads it | close · the landing commit · a revision cycle and its vetter | the owner, at the gate |
| on a re-write | overwrite, except `## Revision rounds`, which only gains a block | overwritten whole: always the item as it stands NOW |

Write the record first. The report is easy once the facts are laid out, and impossible to keep
honest when they aren't.

## Step 1 — Directed reads

- **`artifacts/plan.md`** — the contract this work was measured against.
- **Implementation:** every `artifacts/build-vet-<n>.md`, oldest to newest — what each cycle built,
  what vet found, how it ended. The ARC across them is what the report carries.
- **Research:** `artifacts/investigation.md` and `reports/report-investigate.md`.
- **`artifacts/authorizations.md`** if present, and `checkpoints/` (newest first).
- **The project's own standards — `decisions.md` and `architecture.md`.** The plan says what this
  item owed; these say what the project had already settled, and they are a separate bar. Work can
  do exactly what the plan asked and still cut across a decision nobody revisited, which is the one
  failure a plan-only reading cannot see. Implementation items only: a research item concluded
  nothing into the code.

Numbers come from their source, never an estimate: the diff shape from one
`git diff --stat <base>...HEAD` run in the worktree — `<base>` is the branch base named in your
`## Current focus` block — and check results from the cycles' `## Verification`.

## Step 2 — Name what the anchor docs will owe

Did this item change something the anchor docs describe (project-prd / architecture / capabilities /
decisions / roadmap / resources)? Say so in the report, doc by doc, one line each — the CLOSE run
writes them after the merge locks the code, and it reads this. Nothing doc-worthy changed → say that
too; silence reads as an oversight.

An op that DEFINES or ALTERS intent is not a sync — re-scoping a deliverable, changing a written
success signal, setting direction. Those go through `request_authorization`, naming the ops they
cover. Two changes on one item can need two different calls:

| the change | the call |
|---|---|
| `architecture.md` — record that the CSV writer moved into `reporting/` | name it here; close writes it — it describes what now exists |
| `project-prd.md` — drop `--csv` from deliverable `d-reporting` and rewrite its success signal | `request_authorization` — it changes what the project promised |

The tell is not which doc you touch — it is whether the line you change DEFINES what was promised.

**A research item skips this step:** nothing it concluded has been implemented, so no anchor doc
owes it anything. Its proposals in step 3 are how its findings become work.

## Step 3 — Write the record

`scaffold_artifact(item_id, "review")`, then fill the slots — you get your kind's shape.
Five things it must get right, because each has a named downstream reader:

- **`## Against our own decisions`** — **two bars, judged separately.** The plan says what this item
  owed; `decisions.md` and `architecture.md` say what the project had already settled. Work can pass
  one and fail the other, and each masks the other when they are scored as one verdict: code that
  does exactly what the plan asked while cutting across a settled decision reads as a clean item
  everywhere except here. Name the departure, say what the code does instead, and leave the ruling
  to the owner — a recorded decision can be outgrown, but not silently.

- **`**Delivered:**`** — the kernel writes this line into the landing commit. It outlives this
  workspace by years, so write it as the project's history should read: what the code now does.
- **`## Settled — do not re-open in a revision cycle`** — every question this item closed, with who
  decided and when. A revision cycle reads this to know what is NOT back on the table; omit one and
  the next round re-litigates it for free.
- **`## Proven vs taken on trust`** — cite into the cycles' `## Verification`, never re-transcribe
  it; the ledger is that table's one writer. What you add is the row the ledger structurally cannot
  hold: **the claim nothing covers.** Omitting it is the one failure this phase cannot recover from.
- **`## Revision rounds`** — append-only. First review leaves `_None. First review._`; a re-write
  after a `revise` APPENDS one block and rewrites nothing above it. A superseded decision is marked
  superseded where it stands, never deleted — the record is how it was decided, not how it ended up.

**Research work item only:** `## Proposed work` is the second deliverable. Each proposal is independently
startable (two that cannot begin in either order are ONE proposal, and its plan phase splits it),
checked against the live board so a duplicate is named as one, and never created here. "Nothing
should change" is a finished investigation, not a failed one. Leave `**Owner's decision:**` empty —
`itemize` fills it after the gate.

**Sort every open call before writing the block.** First limb that fits wins:

1. **A matter of fact** — not a call. Go and find out, then write the finding.
2. **A preference whose default is safe and cheap to undo** — yours. State it in
   `**Default applied:**` with what reverses it. The proposal files normally.
3. **A preference whose default is destructive or expensive to reverse** — the owner's. Write
   `**Question:**`, `**Reserved because:**` and `**Suggested:**`. Omit `**Answer:**`.

`**Reserved because:**` takes ONE BARE WORD — `destructive` or `expensive_to_reverse` — and nothing
else. The field is read as a value, not as a sentence, so appending the reason to it fails the gate
and the whole review waits on a revision. The reason belongs in `**Suggested:**`, where the owner
actually reads it.

Never both a default and a question on one proposal. If you cannot name which of the two reasons a
question passes, it is limb 2 — decide it. An unanswered question is not filed as work, so every one
you write either spends the owner's decision or costs that finding its ticket.

Before writing any limb-3 question, `read_decisions`. A ruling already in the ledger IS the answer:
drop the question and write the proposal against that `D-NNN`.

**Bad and good examples**
```example
✗ **Question:** delete the unused `legacy-theme.css`?
  **Reserved because:** expensive_to_reverse     — one revert restores it, so this is limb 2
✓ **Default applied:** deleted the unused `legacy-theme.css`; one revert restores it

✗ **Reserved because:** destructive — the file is untracked, so there is no undo
      — the word is right and the field still fails: everything after it is prose
✓ **Reserved because:** destructive
  **Suggested:** delete — it is untracked, so there is no undo either way; …
```

**When the owner rules, write `**Answer:**` — and `**Rule:**` only if one is there.** Their answer
is spent once the work is done. A rule is kept forever, so it earns its line only by binding work
nobody has proposed yet. Two tests, both of which it must pass:

- **Standalone** — a reader who has never heard of this item can act on it. Name no file this item
  touched, no item id, nothing they must go and look up.
- **Not yet written** — `read_decisions` first. If a `D-NNN` already says it, there is no new rule.

Most rulings establish nothing, and leaving the line out is the correct outcome, not a gap. Writing
one anyway costs more than a missing entry: every later phase reads the ledger before asking
anything, so a rule that overreaches silently suppresses questions that should have been asked.

**Then ask whether the proposal is still work.** An inbox item is a thing that becomes a WORK ITEM
when pushed — that is the whole definition. A ruling of "keep it", "leave it as it is", "no change"
empties the proposal: there is nothing to plan, build or verify. Write `**Becomes work:** no` and
name it under `## Settled` instead. It is not filed, and that is the point: a ticket whose own body
says there is nothing to do still costs the owner a row on their board and a live Push button that
would cut a branch for a no-op.

Do not reach for an inbox item as somewhere to keep a decision. If the ruling is worth remembering
it is a `Rule`; if it is not, it is spent. Neither is work.

```example
Owner ruling: "delete the old exporter, don't leave a stub"

✗ **Rule:** delete the old exporter rather than stubbing it
      — the same instruction reworded; spent the moment that file is gone
✗ **Rule:** prefer deleting dead code
      — true of everything and settles nothing; no future call goes differently
✓ **Rule:** a superseded module is deleted outright — no re-export shim, no tombstone file
      — the next reader meets a superseded module and knows the call without asking
```

## Step 4 — Write the user-facing report

Fill `templates/report-review-template.md` and hand the whole body to `file_review_report`. It owns
the path and refuses a report with an unfilled slot left in it — never write the file yourself. ONE
template for every kind: an implementation item, a research item and whatever kind comes later all answer the
same four questions.

| section | implementation | research |
|---|---|---|
| What you're approving | problem · solution · now vs before · completion | question · how it was answered · what is now known · how much was covered |
| What to push back on | the calls that could have gone the other way | the judgement calls in the method |
| How much to trust it | proven vs taken on trust | measured vs reasoned |
| Where this leaves the project | what pattern it sets, what it left behind | what the answer opens up |

- **Lead with what the owner must decide.** `**Summary:**` is one line, shown alone at the gate, so
  it must stand without the report around it. No verdict word and no recommendation line: the
  Summary says it and the buttons do it.
- **Every line traces** to `artifacts/review.md` or a doc from step 1 — no new facts. `How much to
  trust it` carries the **not covered** rows too; a table of only green rows is an advertisement,
  not a basis for a decision.
- **A departure from a recorded decision gets its own line under `What to push back on`**, named as
  what it departs from — never folded into the plan verdict. Ranking the two bars against each other
  is what lets the stronger one hide the weaker.
- **Four beats, and the third is a CHECK.** `What you're approving` runs `**Problem:**` · `**Worked
  solution:**` · `**Current behaviour:**` · `**Completion:**`. The first two are the owner's own
  framing, one line each. The third is this phase's real job: what the code does NOW against what it
  did before, every line traceable to a `## Verification` run. Reviewing whether the plan was
  followed is not reviewing whether the problem was solved.
- **`**Completion:**` reports state, never a recommendation.** Complete, complete with caveats, or
  incomplete — and always the reason: what is unfinished, what vet or the deputy raised and where it
  landed, what nothing exercised. Push-back carries the calls that could have gone the other way and
  the trust table carries what is proven; this line carries only whether the item is finished.
- **Don't restate the cycles' `## For the reviewer`.** The owner already read those beside the code
  on the PR page; this report answers a different question — whether the item should land at all.
  Mention one only when the DECISION turns on it, and then say what it means for the merge.
- **Re-writing after a `revise`?** Overwrite the file whole and fill `## What you asked for` at the
  TOP: their objection quoted, what the item now does about it, and what that cost. Delete that
  section on a first review.

**Tone and style when writing to user-facing report**
- Plain, easy language. Fewer words wins.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a prose field rather than filling it with "none" — an absent block reads better.

## Chat response style
- Use plain and easy language.
- Keep your response short, clear, and to the point.
- Use bullets or numbered lists to organize information if there is more than one point.
- Do not use more than 30 words.

## Reporting the run

`report_completion` says why THIS RUN stopped, in one line: what is being put to the owner and what
is still open. It is not the gate's answer — nothing here approves anything, and the item sits at
review either way.

**On an implementation item the same call carries `machine.commit`** — how this work should read in
the project's permanent history once it lands. You are the last phase that knows what actually
shipped, so you declare it and the kernel writes it. A research item declares none: nothing lands.

**A run the kernel fired always declares an outcome**, and a run that skips it is recorded as
undeclared. This entry run is one of those. The conversation that follows at the gate is not — those
turns are ordinary chat and owe no report.

**Output style to report_completion.**
- Plain, concise, easy language. Fewer words wins. No verbosity.
- Keep your response short, clear, and to the point.
- Do not use more than 30 words.

## Pitfalls

- **A claim the artifacts don't carry** — "tests pass" either cites the check and its evidence row,
  or says plainly that it is unverified.
- **Restating the plan** — the owner can open `plan.md`. What they cannot reconstruct is the arc:
  what changed across cycles, what it cost, what is still open.
- **Fixing anything** — no code, no plan edit, here or later in this session. If the work must
  change, the owner routes it back.
