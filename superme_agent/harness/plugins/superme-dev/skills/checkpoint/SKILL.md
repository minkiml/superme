---
name: checkpoint
description: Bank this thread's continuity checkpoint — the conversational state that no artifact holds. Use when the kernel asks for one before compacting this session, or before you wrap up a long stretch of work. Not for restating what the plan, the diff or the reports already hold, and not for a phase's own report (each phase files its own).
argument-hint: "[work-item-id | target path]"
category: general
---

# Bank a checkpoint

This thread is about to lose its memory — compacted, or ended. Write down the part of it that
exists **nowhere else**, so whoever picks this thread up next is not guessing.

## What NOT to write

**Anything already on disk. Reference it by path; never copy it.** Restating it wastes the budget
and creates a second version that drifts from the first.

What that rules out depends on the thread:

- **On a work-item** — the goal, the tasks, the decisions of record, the files, the test results,
  what is blocked. So: not the plan, not the diff, not the cycle reports, not the evidence ledger.
- **Without one** — usually much less exists, so this section bites less. Name whatever you wrote
  or edited this session and move on. **Do not spend lines saying which artifacts are absent** —
  "no work-item", "nothing in plan.md", "no code touched" tell the next thread nothing it could
  not see for itself.

## What to write

Five things, because they live only in the conversation:

1. **The last thing the user asked for that you have not yet delivered — in their own words.**
   Quote it. An unanswered question counts: "answer that, with context" is a real outstanding task.
   If they cancelled or changed direction, that reversal is the outstanding item, not the thing it
   cancelled. Write "nothing outstanding" only when the last exchange genuinely closed.
2. **What was cancelled or superseded.** Work that was in flight and is now dead — say so
   explicitly, so nobody finishes it out of momentum.
3. **Dead ends.** An approach that is out, and why. Usually something you tried and abandoned —
   but a direction the two of you talked through and ruled out counts just as much, even if no
   code was ever written. Mark that second kind `Settled — do not re-open`: nothing in the tree
   shows that a path was weighed, so without the line the next stretch proposes it again.
4. **What is now stale.** Anything earlier in this thread that a later turn made untrue — a
   measurement that has since changed, a file that moved, a conclusion you revised.
5. **Answered questions not yet written down.** If the user settled something in conversation and
   it never reached the place decisions of record live, record it here **and route it** — a
   destination, not a flag:

   - On a work-item → `plan.md`'s `## Decisions & clarifications`.
   - Project-wide → **Name what the anchor docs will owe**: which of
     `project-prd / architecture / capabilities` this decision changes, and how.
   - Really work rather than a decision → file it with `create_inbox_item` and say you did. Not
     every thread mounts that tool; where it is absent, name the work in `remaining` instead.
     `work_kind` is REQUIRED: an inbox item is a thing that becomes a work item when the owner
     pushes it, so naming which machinery it becomes is the same act as saying it is work. If the
     thread has not settled which, it has not settled that this is work either — leave it as the
     decision it is, in one of the two destinations above.

   "This was never written down" without a destination is a dead end for whoever reads it.

## How to write it

Four fields. **Where they go depends on which kind of thread this is** — the content contract is
the same either way:

- **A work-item thread** — call `write_checkpoint` with the four fields below.
- **A thread with no work-item** (a general session) — there is no `write_checkpoint` tool here.
  Write the four fields as a markdown file at the path the kernel named in its request — one
  headed section per field, **named exactly as the fields are named below** (`working_on`,
  `remaining`, `decisions`, `notes`), laid out like the worked example at the end of this file.
  Overwrite the file if it exists; read it first if you want to carry anything forward.

The fields:

- **`working_on`** — where this thread actually is, in one or two lines. On a work-item: what you
  were in the middle of, not the phase name. Without one: what this conversation is *about* and
  where it got to — the subject and the state of the thinking. Either way this is a positive
  statement; if no work was under way, say what was being worked out instead.
- **`remaining`** — **item 1 first, in the user's own words**, then anything else the next stretch
  picks up. Their outstanding ask IS what remains, so it leads. Point at `plan.md` for the task
  list rather than reproducing it. use bullet-points if there are multiple items. If nothing remains, say "nothing outstanding." 
  Also belongs here: anything that should become a work-item and hasn't — name it as work to
  itemize, not as a regret that no item exists.
- **`decisions`** — items 2 and 5: what got settled, and what got cancelled. When a decision has
  not yet reached the record, say so **and name where it goes** (per item 5).
- **`notes`** — items 3 and 4: dead ends, and what is now stale.

Two rules on content:

- **Distinguish what you checked from what you believe.** "Tests pass" is a claim the next
  stretch will act on without re-checking. Either cite what proves it (a command, a file, a
  report path) or say plainly that it is unverified.
- **No secrets.** No keys, tokens, passwords, or personal data — write `[REDACTED]`.

Keep it short. A checkpoint that has to be summarized has failed at its job.

## Worked examples

Two threads, the same four fields. Match whichever shape yours is.

### A build thread mid-cycle, about to be compacted

```example

# working_on:
  Cycle 3 of build: making `sum --csv` emit the header on an empty ledger. Header emission
  works; the --month filter path is half-done — writer refactored, tests not updated yet.

# remaining:
  The user's last ask, still unanswered: "can you also make it work when --month filters
  everything out?"
  Then the open boxes in artifacts/plan.md ## Tasks.
  One gotcha before re-running vet: the refactor left `running_cents` initialised AFTER
  `records` is reassigned to the filtered subset.

# decisions:
  User ruled out a --header flag ("no new flags for this"), so the header is unconditional.
  They also settled that an empty ledger prints the header and nothing else, not a 0 row —
  NOT yet in plan.md ## Decisions & clarifications; it belongs there.

# notes:
  Dead end: computing the header inside `_rows()` duplicated it on multi-month input; reverted.
  STALE: the "43 tests pass" figure from cycle 2 predates the refactor — re-run before quoting it.
```

Note what that does **not** contain: no task list, no diff, no test log, no restatement of the
goal. All of it is on disk. What it carries is the ask in their words, a decision that has not
reached the plan yet, a failure worth not repeating, and a number that has gone stale — none of
which any artifact holds.

### A general thread — no work-item, nothing built

```example

# working_on:
  Working out how the ledger CLI should present its output. Nothing implemented; the shape
  the user wants is now settled, the naming is not.

# remaining:
  The user's ask, still open: "later, remind me to rename the sum command to total."
  To itemize: that rename is real work and has no inbox item yet — file one when they confirm.

# decisions:
  No new flags for the output format — ruled out, so any change has to fit the existing ones.
  Project-wide and unwritten: capabilities owes a line that the flag set is closed.

# notes:
  Settled — do not re-open (discussed, never attempted): a separate `--format` subcommand; it
  duplicates what the existing flags already cover.
  STALE: the "43 tests pass" figure they quoted predates the refactor.
```

Note what that does **not** contain either: no line saying there is no work-item, no plan.md, or
no code. The next thread can see that. Every line carries something it could not.

## Then say it

End your turn with a one-line confirmation naming the path you wrote. If the kernel asked for
this before a compaction, that line is the last thing in the transcript before the summary is
taken — so make it state the outstanding item from #1, in the user's words. That sentence is the
most likely part of this whole thread to survive.
