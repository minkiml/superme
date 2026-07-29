---
name: plan
description: Plan a work-item — turn its brief into a design, a task checklist, and the verification plan the build and vet phases run against. Use when a work-item is in its plan phase or the owner asks to plan or design an item; not for classifying a fresh item (use triage) or for implementing an approved plan (use build).
argument-hint: "[work-item-id]"
category: workspace
---

# Plan a work-item

Produce `artifacts/plan.md` — the contract build implements and vet verifies. It is read twice
and both readings must hold: build executes the design and tasks; a fresh vet agent with zero
context executes the verification plan.

## Step 1 — Directed reads

Read `artifacts/brief.md` (the shaped ask — your starting point), the latest `checkpoints/` entry
when one exists (data from a previous session — verify against the repo before trusting), and the
code the brief points at. For prior activity, call `read_dev_log` with this `item_id`. If the
item is not in `plan`, stop and say so.

## Step 2 — Recon before design

- **Straightforward** (clear intent, contained change, one obvious design) → design directly.
- **Complex / ambiguous** (broad blast radius, several viable designs) → fan out parallel
  Explore subagents first (model: sonnet), one per "map how X works today" question — the
  `## Design` section's modules/interfaces come from these answers, not from guesses.

Interactive sessions: iterate the design with the owner until sound before recording. A call the
owner didn't make that would be expensive to reverse → record it in `plan.md`'s
`## Decisions & clarifications` (what · why · cost of being wrong);
it reaches them at the next gate.

## Step 3 — Grill the owner when a decision is genuinely theirs

Never ask what you can look up: a question the codebase, the anchor docs, or a recon subagent can
answer is yours to answer. What remains are the design tree's genuine forks — competing designs
with different costs, intent the brief cannot settle, scope calls — where guessing wrong is
expensive (a call you can make and simply record is not a question). For every
question you do carry, form a **recommended answer** first; a question without a recommendation
is research you haven't finished.

Either way the shape is the same four fields — the question alone, your recommendation, its
one-line ground, and the alternative with the condition that would select it. Keep the question
free of the reasoning that produced it: the owner is deciding, not reading your derivation.

**Interactive session:** grill one question at a time, walking the design tree in dependency
order — an answer often reshapes the next question. For a consequential fork:

```markdown
### <the question, as a question>
- Recommend — <the answer>
- Why — <one line>
- Instead — <alternative>, if <when you'd pick it>
```

**Background run:** you cannot converse — end the run instead of guessing. `report_completion`
with `machine.outcome: needs_user` and one `user.questions` entry per open question; the tool
carries the four fields, so your final message says only what is being asked and stops (the card
already renders them — restating them there is a second copy the owner has to reconcile). One
call carries all questions; each round costs the owner a visit.

Answers arrive later as chat in this same session. Before touching the rest of the plan, record
every settled question into `## Decisions & clarifications` — one entry per question:

```
### <ts> — <the question, one line>
- answer: <the owner's answer, in substance>
- changed: <what it changed in the plan, or "nothing">
```

Then update the affected sections and close the round with `report_completion` again — `success`
when the plan is finished, or `needs_user` with what remains open.

## Step 4 — Author the plan

`scaffold_artifact(item_id, "plan")`, then fill the slots — the scaffold arrives in the shape of
the item's kind. The section contract — what each must achieve, field grammar, and the hard rules
the gate enforces — is `../../references/artifacts.md` § "plan.md — the section contract", one per
kind; read the one you were handed before filling. Judgment bars the reference can't check for you:

- `## Design` is what build implements verbatim — if it outgrows a section, propose splitting
  the item instead of writing a design document.
- Every `## Verification plan` `expect` must be falsifiable: if you can't picture the output
  that FAILS it, rewrite it. The bar for `depth: none` is high — "only a rename" or "only
  frontend" doesn't clear it; `none` is for items with no observable surface at all.
- Hand build only tasks it can complete itself: it can edit code in its worktree and stage
  contract-doc changes, but not perform owner decisions or reach outside its boundary. A KNOWN
  wall is settled here (decide + record the assumption), never left as a mid-build surprise.

## Step 4b — When this is a REVISION, not a first plan

An item arriving here from review carries feedback and an existing `plan.md` that build already
worked against. Change it **only** through `revise_plan` — never rewrite the file, and never
restate a section the feedback didn't touch. Read the feedback, then decide the scope:

- **targeted** — the approach holds, specific things must change. Section ops for prose,
  task-level ops for `## Tasks` (a checkbox is progress build earned; a section rewrite throws it
  away). One op per feedback point, each naming the point it answers.
- **redesign** — the approach itself was wrong. Rewrite the design in place, pass `superseded`
  saying what prior work is void and what build must undo, and the checkboxes reset for you.

Then continue at step 5: the report is written from the revised plan, not from the feedback.

## Step 5 — Write the report, then stop

Copy `templates/report-intake-template.md` (this skill's folder) to `reports/report-plan.md` and
fill it as the Plan variant — every line traces to plan.md; the template's caps are the bar.
State in one line what the plan will build and stop — the owner (or the deputy) advances the
phase.

## Pitfalls

- **A checklist outside `## Tasks`** — progress is derived from that section's checkboxes only.
- **A vague `expect`** — "works correctly" gives the vet agent nothing to falsify.
- **Interrogating an easy task** — recon fan-out and long deliberation are for genuine forks.
