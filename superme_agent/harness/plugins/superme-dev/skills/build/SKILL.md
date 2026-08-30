---
name: build
description: Implement a build-phase work-item inside its git worktree — work the plan's task checklist, run internal validation, record the cycle report. Use when a work-item is in its build phase and code is to be written; not for planning, verifying finished work (use vet), or research items (use investigate).
argument-hint: "[work-item-id]"
category: workspace
---

# Build a work-item

Turn `artifacts/plan.md` into working code inside this item's git worktree. The plan is the
contract: `## Design` is what you implement, `## Tasks` is the tracker you tick, and
`## Verification plan` is the exam a separate vet agent runs against what you produce. Those two
live sections sit LAST in the file — they are the current truth, and nothing above them overrides
them.

**Is there a `## Revision r<n>` block newer than the cycle you last worked?** Read it FIRST, before
anything else:

- `directive` — what this generation does differently.
- `still in force` — what earlier revisions still bind. Read it; the older blocks are history, that
  line is not.
- A `redesign` change says what of your own work is void and must be undone FORWARD — new commits
  that revert, never a reset or a force-push.
- `## Tasks` is the only task authority. A revision may have removed tasks you already ticked.

## Contract

**Minimum code that solves the problem. Nothing speculative.**
- No features beyond what was asked.
- No error handling for impossible scenarios.

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't write heavy and verbose comments, be concise and minimalistic.
- Don't widen scope from what is planned.
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Don't add features nobody asked for, don't handle cases that cannot be reached, and don't change
  behaviour that was never discussed — do not over-complicate or over-think.
- Match existing style, even if you'd do it differently.
- If you notice dead code, report it — don't delete it. **Caution**: a function or API may look dead
  (unused anywhere in the codebase) when it is actually being used from an external source (e.g., an
  externally-invoked API like QR code) — check the contents and logic of looking-dead code before
  calling it dead.
- If you write 200 lines and it could potentially be 100 or even less, rethink and rewrite it. Ask
  yourself: "Would a professional senior engineer say this is overcomplicated?" If yes, simplify.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.

## Step 1 — Directed reads

Your trigger names the work order — the plan (opening cycle), a failed cycle's report, or a routed
review change. Beyond it:

- **A fresh session with prior work** → the latest `checkpoints/` entry, plus `git status` in the
  worktree. The checkpoint is data from a previous session; verify it against the tree before
  trusting it.
- **A failure hop** → the prior `artifacts/build-vet-*.md` files carry what already happened.

## Step 2 — Work task by task

For each task: implement → verify it does what the task says (if it fails, back to implement and fix
until it passes) → tick its checkbox in `plan.md` (`- [x]`) → commit. Never tick a box for
partially-done work: the progress the owner watches is derived from exactly these boxes.

Every commit splits in two, and that split is the whole rule:

```
Add a --category flag to tally

Only totals rows whose category matches; an unknown category is an
error rather than an empty report, so a typo stays visible.

SuperMe-Task: t3
```

Above the blank line is for the PROJECT — whoever reads this repository's history has never heard of
this workspace, so no task ids, item ids or phase names in the subject or body. The trailer block is
for SuperMe: `SuperMe-Task: t<n>`, on its own final line, is the ONLY thing joining the commit to
its task, and the review page walks the diff by reading it. A commit without one lands in an
"unlabelled" pile that tells the owner nothing. `references/commit-style.md` has the rest of the
shape — when to write a body, the `(wip)` marker, naming a check you're fixing.

A commit that git refuses is never retried blind. Read the refusal:

- It names the missing task trailer → add it and commit again. That one is yours.
- Anything else — a check this project owns — is not yours to overrule. Do not try variations, and
  never `--no-verify` (it is denied). Leave the work staged and end the run `needs_user`, quoting
  the refusal verbatim and naming what you think the owner should do (ask their team, change a
  setting, drop the rule). The item parks there; nothing advances on work that cannot land.

Four sections are never yours to write:

| section | whose | why |
|---|---|---|
| `plan.md ## Design` | plan's | a design that no longer fits reality goes back through plan — say exactly what broke instead of silently diverging |
| `plan.md ## Verification plan` | plan's | the exam. Re-pointing your own checks to dodge a wall is the self-grading the vetter exists to catch — defer it, don't disguise it |
| the cycle report's `## Verification` | vet's recording tool | a hand-written line there is evidence nobody produced |
| the cycle report's `## Cycle outcome` | the loop driver | same |

On long builds, sync with the trunk via `sync_from_anchor_branch` (commit first) and resolve any
conflicts it reports yourself.

### Tag every probe, and the cleanup is one grep

Print statements, temporary logs, a hardcoded value you dropped in to watch something — all of it is
fine while you are working, and all of it is invisible by the time you are three tasks further on.
So give every temporary line a tag the moment you write it:

```python
print(f"[DEBUG-a4f2] cursor={cursor} rows={len(rows)}")   # ← one tag per debugging session
```

**Pick four hex characters once per cycle and reuse them.** Before you fill the cycle report, run
`grep -rn "\[DEBUG-" .` in the worktree — an empty result is the check, and a hit is a line to
delete. Untagged instrumentation survives; tagged instrumentation dies on one command.

The review gate greps your branch's added lines for the same tag, so a probe you meant to remove
arrives at the owner's gate naming its own file and line.

## Step 3 — Walls become records, never a stall

The owner is not watching; nothing you ask mid-run reaches them. So decide and record:

- **An unknown the plan didn't settle**, where your choice is expensive to reverse or changes what
  the owner receives → a `## Assumptions` entry in the cycle report (what · why · cost of being
  wrong). Skip trivia — twenty non-decisions bury the two that mattered.
- **A change to what the project INTENDS** (renaming or re-scoping a deliverable, a
  direction-setting decision, deleting or editing a retired doc) → `request_authorization`
  (what · why · doc · scope · the check it blocks). The blocked check DEFERS and the request rides
  to review, where the OWNER answers it — a grant is performed by close after the merge, a denial
  accepts the gap. Nothing comes back to you either way, so finish and report.
  Reconciling a doc to what you actually shipped is NOT this: close writes that, and asks nobody.
- **Work that must be fixed first** → `create_inbox_item` with relation `blocking`; worth doing but
  not now → relation `spawn`. Never absorb out-of-scope work into this worktree. Pass
  `work_kind: "implementation"` — a branch-off from a build is code unless what you actually hit
  was a question nobody can answer yet, which is `research`.

Either way, finish every OTHER task. A wall on some tasks reports `partial`, never `blocked`.

## Step 4 — Validate, then record the cycle

### 4a — Run your validation, and record each run

Run the repo's tests/lint/typecheck plus whatever proves each task (mocks, synthetic errors). Fix
what they catch: a cycle handed to vet with red basics burns a whole vet run to learn what an exit
code already said.

**Call `record_validation` for each one.** This is not a gate on you — you run what you judge is
needed. It is what turns "the suite passed" from a sentence only you witnessed into something
verification can hold up against the machine.

- **Record the command verbatim and re-runnable.** Vet re-executes what you recorded and compares
  the result to what you claimed, so an abbreviated, aliased or shell-specific command reads as a
  disagreement you never had.
- **Record the reds too.** A failure you then fixed is a real part of the cycle, and the last
  recorded run for a command is the one that counts.
- **A command that needs a running server must boot it itself.** Your trigger carries the exact
  start/stop pair when this repo has one.

### 4b — Fill the cycle report

Fill the current `artifacts/build-vet-<n>.md` (highest n — the kernel scaffolded it): `## Built`,
`## For the reviewer`, `## Validation`. The vetter reads these instead of re-deriving your work from
a raw diff — name files, how to exercise the change, and every gap honestly.

- **Every per-task bullet LEADS with its task id** (`- t2 — …`), the same id as the commit trailer.
  That join is what lets the owner's Proof view say "this feature, proven this way". Item-wide work
  (a shared refactor, a stale doc fixed in passing) leads with no id.
- **`## For the reviewer` is the only thing you write that a person reads beside your diff.** One
  line per task, saying what the diff cannot: the case you chose not to handle, the value nobody
  specified, the call that could have gone either way. `look: none` is a real answer, and most tasks
  are that. Say `deviated:` whenever you built something other than what `plan.md` specified — a
  reviewer can reconstruct what you did, never what you were supposed to do instead.
- **A cycle with nothing to build still fills them** — "nothing: r3 was a plan-text fix" is an
  answer; a leftover `<fill:…>` reads as a build that gave up.
- **An anchor doc your change made wrong** → note it in `## Built`. You never edit those; close
  writes them once the merge locks what you did.

### 4c — Write the user-facing report

Read `templates/report-build-template.md`, fill every `<fill:…>` slot, and hand the whole
body to `file_phase_report`. It owns
the path, overwrites, and refuses a report with an unfilled slot left in it — never write the file
yourself. Every line traces to the cycle reports. It always describes the work as it stands NOW, so the round history
goes in the `**Summary:**` line and nowhere else ("Done, after three rounds — the empty-ledger case
took two tries"), because that line is what the dashboard shows on its own.

- **`## Checked as I went` is YOUR checks, in the owner's words** — what you exercised and what it
  did, never the command. The independent pass is vet's report, and the two must not read as the
  same list.
- **A spawn the owner cannot otherwise learn about goes in `## Work this turned up`.** Build runs
  unattended, so this is the only place a blocking spawn reaches them. Say what it is, why it
  couldn't live here, and whether this item waits on it. Never print the new item's id. Delete the
  whole section when nothing was filed.

**Tone and style when writing to user-facing report**
- Bullets, not paragraphs. One fact per bullet, each under 20 words.
- They are coming back cold to decide something. Give the decision, not the derivation.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a field rather than filling it with "none" — an absent block reads better.

## Reporting the run

`report_completion` says why THIS RUN stopped — and here it also STEERS, because the loop reads the
outcome to decide what happens next. Three readings, and only two of them stop the loop:

- **`needs_user`** — the one wall that parks a build: a commit this project's own checks refused.
  Nothing landed, so advancing would vet a tree whose content cannot reach review, and the owner
  would meet an empty diff a cycle later.
- **`revise`** — the plan itself cannot be built as written: its design contradicts the code, or its
  `run:` commands point at the wrong tree. You are guarded out of amending it, so this routes the
  item back to plan. Say what broke, where, and why the plan's version cannot work.
- **`success` · `partial` · `blocked` · `clean_noop`** — all advance to vet, identically. Pick the
  honest one for the record; none of them holds the item back. That is the point of not paging: a
  recorded gap rides to review on a real diff, where the owner can judge it.

Bank a `write_checkpoint` (what you're on · decisions · remaining · tried-but-failed) before you
report on a session that stopped mid-work. The loop vets what you produce automatically — never
advance the phase yourself.

**A run the kernel fired always declares an outcome.** It is the only thing the kernel reads to
learn what happened, and a run that skips it is recorded as undeclared.

**Output style to report_completion.**
- Plain, concise, easy language. Fewer words wins. No verbosity.
- Keep your response short, clear, and to the point.
- Do not use more than 30 words.

## Pitfalls

- **Ticking boxes optimistically** — an unverified tick corrupts the one progress signal.
- **Riding through a stale plan** — divergence between plan and code makes every later gate wrong;
  route the amendment through plan first.
- **Reporting a wall instead of recording it** — a wall that isn't in the assumption or
  authorization ledger is invisible at review.
- **Building more than the plan asked for** — an improvement nobody planned has no task to tick, no
  check to prove it, and lands in the diff as a surprise.
