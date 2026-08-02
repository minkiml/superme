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

Read `artifacts/brief.md` (the problem this item exists to solve — your starting point), the latest `checkpoints/` entry
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
- Give a check a `run:` line whenever one command can decide it — the kernel runs it in the sandbox
  before vet opens, so it costs nothing, re-runs free on every cycle, and lands as machine evidence.
  Write it as one command whose **exit code is the verdict** (`&&` chains steps; a grep that must
  match is `... | grep -q thing`). Omit `run:` when the pass condition needs a person or a subagent
  to judge it — a UI that must look right, a message that must read well, a design bar. Never bend a
  judgment call into a command to earn the label; a wrong green is worse than an honest attestation.
- Each check's `covers:` names the task id(s) it proves. This is what lets the owner's Proof view
  read "this feature, proven this way" instead of a bare grid of check ids — the task id is the
  join key across the plan, the cycle reports, and the ledger. A genuinely whole-item check (the
  suite, a lint pass) leaves it blank; do not invent a task for it. A check covering a task no
  `## Tasks` line declares is a check for work nobody planned — fix one or the other.
- Hand build only tasks it can complete itself: it can edit code in its worktree and stage
  contract-doc changes, but not perform owner decisions or reach outside its boundary. A KNOWN
  wall is settled here (decide + record the assumption), never left as a mid-build surprise.

## Step 4b — When this is a REVISION, not a first plan

An item arriving here from review carries feedback and an existing `plan.md` that build already
worked against. Change it **only** through `revise_plan` — never rewrite the file.

**Split the feedback into concerns first.** One review conversation usually carries several: the
loop hit its budget AND two checks failed AND the caching approach was wrong. Each concern becomes
one entry in `changes` with its **own** scope — so redesigning one part never resets the progress
another part earned:

- **resume** — the plan was right; run another generation against it unchanged. No ops, and an edit
  here is refused. This is the honest answer to *"looks close, try more"*.
- **targeted** — right in approach, wrong in places. Section ops for prose, task-level ops for
  `## Tasks` (a checkbox is progress build earned).
- **redesign** — the approach itself was wrong. Rewrite the section, name what is void in
  `superseded`, and remove the dead tasks EXPLICITLY (`remove_task` + `add_task`) — nothing resets
  for you, because a guess is worse than the exact list.

**The proportionality rule is a refusal, not advice.** If a concern needs no plan change, its scope
is `resume` — do not manufacture an edit to have something to show, and do not re-instruct build on
parts nobody complained about. Over-modification is the failure this grammar exists to prevent.

Two fields carry the instruction: `directive` (what the next build does DIFFERENTLY — the one line
it acts on) and `still_in_force` (what earlier revisions still bind; `nothing` on the first). Build
reads only the newest block, so `still_in_force` is what keeps that honest.

You do not tag the concern types or the budget — code reads those off the loop's exit and the
authorization ledger. Your revision opens a fresh build⟷vet generation.

Then continue at step 5: the report is written from the revised plan, not from the feedback.

## Step 5 — Write the quick report 

Copy `templates/report-plan-template.md` (this skill's folder) to `reports/report-plan.md` and
fill it — every line traces to plan.md; the template's caps are the bar.

**Writing tone and style**
- Plain, easy language. Fewer words wins.
- Keep the report short, clear, and to the point.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Prefer a table to a paragraph whenever the content is pairs or a list of comparable things.
- Use bullets and numbered lists to organize information if there is more than one point.

## Chat response style
- Use plain and easy language.
- Keep your response short, clear, and to the point.
- Use bullets or numbered lists to organize information if there is more than one point.

## Pitfalls

- **A checklist outside `## Tasks`** — progress is derived from that section's checkboxes only.
- **A vague `expect`** — "works correctly" gives the vet agent nothing to falsify.
- **Interrogating an easy task** — recon fan-out and long deliberation are for genuine forks.
