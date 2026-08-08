---
name: plan
description: Plan a work-item — turn its brief into a design, a task checklist, and the verification plan the build and vet phases run against. Use when a work-item is in its plan phase or the owner asks to plan or design an item; not for classifying a fresh item or for implementing an approved plan.
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

Then read `reports/report-triage.md` § **From you**. It is the owner's own section, typed by them
and by nobody else, and it is the only place their words arrive as instruction rather than as chat —
so it outranks your judgment on the two things it carries:

Each block holds one bullet per entry — a reference is its source and what it governs; a note is
one thing to prove.

- **Useful imported references** are AUTHORITY. Open each one and design to it. Where it and your
  preferred approach disagree, the reference wins and the design says so; if following it is
  impossible or would break something, that is a question for step 3, not a call you make silently.
- **Verification notes** each become a check in `## Verification plan` — one note, one check, its
  `proves:` written in the owner's own terms. A note you cannot turn into a falsifiable check is a
  question for them, not a note to drop.

Empty is the common case and needs no comment. Never write into this section: the editor is its
only writer, and a line you add there would come back to you next cycle as the owner's instruction.

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
question you do carry, form a **recommended answer** first with a good concise reasoning; a question without a recommendation
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

- **A task is a NAME on its head line, and its SPECIFICATION on the indented lines under it.** The
  spec goes underneath and can be as long as build needs; the head line is what the owner's board
  shows.

  **Name the CHANGE, not the code.** The owner reads this list to see what the item is doing to
  their product, so a task is named the way they would say it: `Rename a category across the
  ledger`, not `storage.rename_category(old, new)`; `Wire the search subcommand into the CLI`, not
  `commands.search(args)`. A function signature is addressing — it belongs in the spec below, where
  build needs it. A few words, under ~60 characters, no closing period.

  Two ways to get it wrong, both seen live: letting the head line run into the spec's first clause
  (it lands on the Task tab as "…positional `text`, `--month`, `--from`," and names nothing), and
  naming the symbol you are about to write instead of the change you are making. The test: read the
  head line alone and ask whether the OWNER learns what this task does.

- Each check's `proves:` is the one line written for the OWNER — what is true of the product when
  that check passes, in the product's own words. Test it by covering the rest of the block: if the
  sentence still says something, it is one. "With `--quiet`, `count` prints nothing at all" passes
  that test; "exit code is 0", "the suite passes", "the flag is honoured correctly" do not. A
  whole-item check earns the same sentence — "nothing that worked before stopped working". This
  line leads the owner's reports and the Proof view, and it is what tells a vetter whether a green
  actually demonstrates the intent; nobody downstream can recover it from `run:`.

- Every `## Verification plan` `expect` must be falsifiable: if you can't picture the output
  that FAILS it, rewrite it. The bar for `depth: none` is high — "only a rename" or "only
  frontend" doesn't clear it; `none` is for items with no observable surface at all.

- Give a check a `run:` line whenever one command can decide it — the kernel runs it in the sandbox
  before vet opens, so it costs nothing, re-runs free on every cycle, and lands as machine evidence.
  Write it as one command whose **exit code is the verdict** (`&&` chains steps; a grep that must
  match is `... | grep -q thing`). **It already runs in THIS item's worktree — never `cd` and never
  write an absolute path into it.** Both leave the worktree for the repo's primary checkout, which
  is a different git worktree sitting on the anchor branch without this item's commits, so the
  command grades code the item did not write. Every path is relative to the repo root. Omit `run:` when the pass condition needs a person or a subagent
  to judge it — a UI that must look right, a message that must read well, a design bar. Never bend a
  judgment call into a command to earn the label; a wrong green is worse than an honest attestation.

- Give a check a `rubric:` when one pass/fail line can't hold the bar — the criteria are judged and
  recorded one by one, so a failure names WHICH one missed instead of "it didn't look right". Every
  criterion must be able to come back missed; "the code is clean" cannot. **No quotas** — never
  write "find at least two problems": a criterion that demands findings manufactures them. State
  what must be true, and let a clean pass be a clean pass. A check may carry both `expect` and a
  rubric (an exit code AND a judgment about what it printed), and one of the two is required.

- **Before authoring a check, call `read_verification_library`.** The standing entries are already
  in your scaffold — leave them exactly as they are, they are what this repo always owes. If an
  available entry already covers what you were about to write, paste its block instead and mark it
  `- source: library`: an entry there has run and passed, which is more than a check you invent
  here can claim. Cite only what genuinely fits; a near-miss entry is a check that will fail for a
  reason nobody cares about.

- **Never make the project's test suite a check.** `pytest`, `npm test`, `python -m unittest
  discover` — running the whole suite is BUILD's validation: it does it every cycle, and the kernel
  re-runs what it recorded to audit the claim. As a check it runs the suite twice and files a
  validation result as this item's own proof. It is REFUSED at the gate. A single test that drives
  the one behaviour this item promises is a different thing and perfectly good — narrow the command
  (`-k`, a node id, one file) and say in `proves:` what its green means for the owner.

- **Then call `dry_run_checks` and read the exit codes.** It runs only the `run:` blocks you just
  wrote and records nothing. A failing assertion is EXPECTED — the work does not exist yet. What
  you are looking for is a command that could not run at all: a usage error, an import error, a
  path that is not there. That one will never come back green however well build does its job, and
  finding it now costs a second instead of a whole build⟷vet cycle.

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

## Step 5 — File the quick user-facing report

`file_plan_report` writes `reports/report-plan.md` — the owner's read of the plan, in their words,
and the last thing between this plan and the gate. The **confirmation table** is DERIVED: one row
per check, its `proves:` line verbatim beside how it will be run. Nothing you write reproduces it.

You supply the prose: `summary` (one line, and the dashboard shows it alone, so it must stand
without the rest), `approach`, `confirm`, and `decisions` / `assumptions` when there is something
real for them. On an implementation item `confirm` is **what the checks will not tell you** — the
paragraph under the table; on a research item it is *how we'll look, and what we won't*, since
there is no table.

The tool tells you how many tasks have **no check**, and the report names them. That is the plan
gate's first question, so answer it before it is asked: add the missing check, or be ready to say
why that task needs no proof. Never write a check you don't believe in just to clear the count.

**On a revision this report is still about the PRODUCT.** It describes the plan as it now stands —
what is being built and what will prove it — exactly as a first plan does. What CHANGED, and why,
is the record's job (`## Revision r<n>`), not the owner's. Never narrate the workflow here: no
cycles, no phases, no "no plan change needed", no "the loop skipped vet". A live one read
*"No plan change needed — this cycle routes through vet to record them"*, and its reader learned
nothing about the feature they were about to approve. If a revision genuinely changed nothing, the
report is the same report; that is the honest answer, not a paragraph explaining why it is.

**Writing tone and style**
- Plain, easy language. Fewer words wins.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a prose field rather than filling it with "none" — an absent block reads better.

## Chat response style
- Use plain and easy language.
- Keep your response short, clear, and to the point.
- Use bullets or numbered lists to organize information if there is more than one point.

## Pitfalls

- **A checklist outside `## Tasks`** — progress is derived from that section's checkboxes only.
- **A vague `expect`** — "works correctly" gives the vet agent nothing to falsify.
- **Interrogating an easy task** — recon fan-out and long deliberation are for genuine forks.
- when writing to docs, Do not include the comments part `<!-- ... -->` in the scaffold you file — it is instructions for you.