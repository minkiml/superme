---
name: plan
description: Plan a work-item — turn its brief into a design, a task checklist, and the verification plan the build and vet phases run against. Use when a work-item is in its plan phase or the owner asks to plan or design an item; not for classifying a fresh item or for implementing an approved plan.
argument-hint: "[work-item-id]"
category: workspace
---

# Plan a work-item

Decide HOW this item's request gets built — the approach, the order, and what would show it worked.
`artifacts/plan.md` is where that decision is written down so build can execute it and a vet agent
with zero context can check it; the phase is the thinking, and the document is its record. Size both
to the request: a one-line fix earns a one-line design.

**Arriving from review with feedback?** This is a REVISION: read `references/revising-a-plan.md`
before touching anything, then rejoin at step 5. Never rewrite `plan.md` by hand.

## Step 1 — Directed reads

- **`artifacts/brief.md`** — the problem this item exists to solve. Your starting point.
- **The latest `checkpoints/` entry**, when one exists — data from a previous session; verify it
  against the repo before trusting it.
- **The code the brief points at.**
- **`read_dev_log`** with this `item_id`, for prior activity.

If the item is not in `plan`, stop and say so.

Then read `reports/report-triage.md` § **From you**. It is the owner's own section, typed by them and
by nobody else, and the only place their words arrive as instruction rather than as chat — so it
outranks your judgment on the two things it carries:

- **Useful imported references are AUTHORITY.** Open each one and design to it. Where it and your
  preferred approach disagree, the reference wins and the design says so. If following it is
  impossible or would break something, that is a question for step 3, not a call you make silently.
- **Verification notes** each become one check, its `proves:` written in the owner's own terms. A
  note you cannot turn into a falsifiable check is a question for them, not a note to drop.

Empty is the common case and needs no comment. Never write into this section: the editor is its only
writer, and a line you add would come back next cycle as the owner's instruction.

## Step 2 — Recon relevant information before design

- **Straightforward** (clear intent, contained change, one obvious design) → design directly.
- **Complex or ambiguous** (broad blast radius, several viable designs) → fan out parallel Explore
  subagents first (model: sonnet), one per "map how X works today" question. The `## Design`
  section's modules and interfaces come from those answers, not from guesses.

Interactive sessions: iterate the design with the owner until it is sound before recording. A call
the owner didn't make that would be expensive to reverse goes in `## Decisions & clarifications`
(what · why · cost of being wrong); it reaches them at the next gate.

## Step 3 — Grill the owner when a decision is genuinely theirs

- Never ask what you can look up: a question the codebase, the anchor docs, or a recon subagent can
answer is yours to answer. What remains are the design tree's genuine forks — competing designs with
different costs, intent the brief cannot settle, scope calls — where guessing wrong is expensive. 
- A call you can make and simply record is not a question. 
- Form your recommended answer before you ask; a question without one is research you haven't finished.

**Interactive:** grill question(s) at a time, in dependency order — an answer often reshapes the
next question. For a consequential fork:

```markdown
### <the question, as a question>
- Recommend — <the answer>
- Why — <one line>
- Instead — <alternative>, if <when you'd pick it>
```

**Background:** you cannot converse, so end the run rather than guess. `report_completion` with
`machine.outcome: needs_user` and one `user.questions` entry per open question. One call carries all
of them; each round costs the owner a visit.

Answers arrive later as chat in this same session. Before touching the rest of the plan, record
every settled question in `## Decisions & clarifications` — one entry each:

```
### <ts> — <the question, one line>
- answer: <the owner's answer, in substance>
- changed: <what it changed in the plan, or "nothing">
```

The tool comes back into it only when the questions are settled and the plan is finished
(`success`), or when this round left something still open (`needs_user`). Answering a question is
not itself a reportable event.

## Step 4 — Author the plan

`scaffold_artifact(item_id, "plan")` hands you the shape of this item's kind. What each section must
achieve, every field's grammar, and the hard rules the gate enforces are in
`../../references/artifacts.md` § "plan.md — the section contract" — read the entry for the kind you
were handed. The bars below are the judgment that contract cannot check.

### 4a — Decide how much proof this item owes

**Verification is not compulsory.** `depth:` is your call, and the `reason:` line beside it is what
the owner accepts or vetoes at the gate:

- **`none`** — nothing this item does is observable: a rename, a comment, a dependency bump. Vet
  still runs and still reads the diff through its three standing lenses; it just has nothing to
  execute, and files the report saying so. The gate REFUSES a `none` plan that lists checks.
- **`checks` / `scenarios`** — there is a behaviour someone could watch change.

The bar cuts both ways, and both failures are real: do not manufacture checks so a trivial item
looks thorough, and do not call a behaviour change unobservable to skip the work. "Only a rename"
earns `none`; "only frontend" does not.

### 4b — Reuse a library check before inventing one

Call `read_verification_library`. Standing entries are already in your scaffold — leave them exactly
as they are; they are what this repo always owes. If an available entry covers what you were about to
write, paste its block and mark it `- source: library`: an entry there has run and passed here, which
is more than a check you invent now can claim. Cite only what genuinely fits — a near-miss entry
fails for a reason nobody cares about.

### 4c — Write the design, the tasks, and the checks

**A task is a NAME on its head line and its SPECIFICATION on the indented lines under it.**

- The spec can be as long as build needs; the head line is what the owner's board shows.
- **Name the CHANGE, not the code** — `Rename a category across the ledger`, not
  `storage.rename_category(old, new)`. A signature is addressing; it belongs in the spec below.
- A few words, under ~60 characters, no closing period.
- Seen live: a head line that runs into the spec's first clause lands on the board as
  "…positional `text`, `--month`, `--from`," and names nothing.
- The test: read the head line alone and ask whether the OWNER learns what this task does.

Then the checks. Each field's bar:

| field | the bar | the test |
|---|---|---|
| `proves:` | one line for the OWNER — what is true of the product when this passes, in the product's words | cover the rest of the block: does the sentence still say something? "With `--quiet`, `count` prints nothing at all" passes; "exit code is 0", "the suite passes", "the flag is honoured correctly" do not |
| `expect:` | falsifiable | can you picture the output that FAILS it? If not, rewrite |
| `run:` | one command whose exit code is the verdict (`&&` chains steps; a grep that must match is `… \| grep -q thing`) | give it one whenever a command can decide the check — the kernel runs it in the sandbox before vet opens, so it costs nothing and re-runs free every cycle |
| `rubric:` | for when one pass/fail line can't hold the bar — criteria judged and recorded one by one, so a failure names WHICH one missed | can every criterion come back missed? "The code is clean" cannot. **No quotas** — "find at least two problems" manufactures findings |
| `covers:` | the task id(s) this check proves — the join key across the plan, the cycle reports and the ledger | a genuinely whole-item check (the suite, a lint pass) leaves it blank; a check covering a task no `## Tasks` line declares is a check for work nobody planned |

A check needs `expect`, a rubric, or both. It may carry both — an exit code AND a judgment about what
it printed.

Three rules that override the table:

- **A `run:` block already runs in THIS item's worktree — never `cd`, never an absolute path.** Both
  leave for the repo's primary checkout, a different worktree sitting on the anchor branch without
  this item's commits, so the command grades code the item did not write. Every path is relative to
  the repo root.
- **Omit `run:` when the pass condition needs a person or a subagent to judge it** — a UI that must
  look right, a message that must read well, a design bar. Never bend a judgment call into a command
  to earn machine evidence; a wrong green is worse than an honest attestation.
- **Never make the project's test suite a check.** `pytest`, `npm test`, `python -m unittest
  discover` — the whole suite is BUILD's validation: it runs it every cycle, and the kernel re-runs
  what it recorded to audit the claim. As a check it runs the suite twice and files build's own work
  as this item's proof, so the gate REFUSES it. One test that drives the behaviour this item promises
  is a different thing and perfectly good — narrow the command (`-k`, a node id, one file) and say in
  `proves:` what its green means for the owner.

Finally: **hand build only tasks it can complete itself.** It edits code in its worktree and stages
contract-doc changes; it cannot make an owner's decision or reach outside its boundary. A KNOWN wall
is settled here — decide and record the assumption — never left as a mid-build surprise.

### 4d — Dry-run the `run:` blocks

`dry_run_checks` runs only the blocks you just wrote and records nothing. A failing assertion is
EXPECTED — the work does not exist yet. What you are looking for is a command that could not run AT
ALL: a usage error, an import error, a path that is not there. That one will never come back green
however well build does its job, and finding it now costs a second instead of a whole build⟷vet
cycle.

## Step 5 — File the user-facing report

`file_plan_report` writes `reports/report-plan.md` — the owner's read of the plan, and the last thing
between it and the gate. The **confirmation table is DERIVED**: one row per check, its `proves:` line
beside how it will be run. Nothing you write reproduces it.

You supply the prose:

- **`summary`** — one line. The dashboard shows it alone, so it must stand without the rest.
- **`approach`** — the plan in the owner's terms.
- **`confirm`** — **what the checks will not tell you**, the paragraph under the table.
- **`decisions` / `assumptions`** — only when there is something real for them.

The tool tells you how many tasks have **no check**, and the report names them. That is the plan
gate's first question, so answer it before it is asked: add the missing check, or be ready to say why
that task needs no proof. Never write a check you don't believe in to clear the count.

**On a revision this report is still about the PRODUCT** — what is being built and what will prove
it, exactly as a first plan reads. What changed, and why, is the record's job. Never narrate the
workflow here: no cycles, no phases, no "no plan change needed". A live one read *"No plan change
needed — this cycle routes through vet to record them"*, and its reader learned nothing about the
feature they were about to approve. If a revision genuinely changed nothing, the report is the same
report.

**Tone and style when writing user-facing report**
- Plain, concise, easy language. Fewer words wins. No verbosity.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a prose field rather than filling it with "none" — an absent block reads better.

## Chat response style
- Plain, concise, easy language. Fewer words wins. No verbosity.
- Keep your response short, clear, and to the point.
- Use bullets or numbered lists to organize information if there is more than one point.

## Reporting the run

`report_completion` says why THIS RUN stopped. A run is one invocation of you — it is not the phase,
and it is not always the end of the work. The outcome word is what separates them:

- **`success`** — you stopped with the plan authored and its report filed. It still does not mean
  the phase is approved or the item advanced; that stays the owner's call at the gate.
- **`needs_user`** — you stopped at step 3 with questions open. The RUN is over (nobody is there to
  answer); the WORK is not. The kernel parks the item and shows your questions.
- **`partial` · `blocked` · `clean_noop`** — when one of those is the truer word for why you stopped.

**A run the kernel fired always declares one.** It is the only thing the kernel reads to learn what
happened, and a run that skips it is recorded as undeclared.

**A conversation does not.** When the owner is answering in the thread, each reply is an ordinary
chat turn and owes no report — not per question, not per round. Report again only when the item's
state actually changes: `success` once the plan is finished. Reporting every round re-parks an item
that is already parked, and a premature `success` un-parks one whose questions are still open.

**Output style to report_completion.**
- Plain, concise, easy language. Fewer words wins. No verbosity.
- Keep your response short, clear, and to the point.

## Pitfalls

- **A checklist outside `## Tasks`** — progress is derived from that section's checkboxes only.
- **A vague `expect`** — "works correctly" gives the vet agent nothing to falsify.
- **Interrogating an easy task** — recon fan-out and long deliberation are for genuine forks.
- **Over-complicating and over-engineering the plan** — If the goal of this work item is simple and straightforward, do not overthink it, do not over-plan, and do not over-state in the docs.
