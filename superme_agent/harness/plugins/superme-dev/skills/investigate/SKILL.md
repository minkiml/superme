---
name: investigate
description: Run a research work-item's investigation — set the questions and the walls, then answer them with evidence recorded in the item folder. Use when a research work-item is in its investigate phase; not for implementing anything (use build) or for drawing the conclusions (use review, at the review phase).
argument-hint: "[work-item-id]"
category: workspace
---

# Investigate

Answer a research work-item's questions with evidence, and leave the record someone else acts from.

**You need:** the work-item id and its investigation family — both in your Current focus block.
**You may not:** change any code, or write anywhere but the item folder.
**Every claim you record carries a pointer**: `file:line`, a URL with the date you read it, or a
command and its output.

Have this in your mind to keep track of and tick each line as you progress:

```
Investigation progress:
- [ ] 1 <family> guide read and follow
- [ ] 2 Record scaffolded, questions and walls and Done written
- [ ] 3 Surface split, investigators spawned
- [ ] 4 Receipts verified at their own lines
- [ ] 5 Record filled
- [ ] 6 Follow-up work sized
- [ ] 7 Report written, checkpoint banked
```

## 1 · Must read your family's guide

Open `references/<family>.md` for the family named in your Current focus block — before the brief,
before any code. It gives you the **bar & absolute guidance** to follow: what counts as a finding here, and what counts as proof.

| family | guide |
|---|---|
| audit | `references/audit.md` |
| refactoring | `references/refactoring.md` |
| housekeeping | `references/housekeeping.md` |
| security | `references/security.md` |
| study | `references/study.md` |
| deep-diagnosis | `references/deep-diagnosis.md` |

If your item names no family, pick the closest one from what the item asks for, and say which you
picked in `investigation.md`. If the work turns out to be a different family than the item says, say
so in your report.

## 2 · Scaffold the record, and write the questions

Call `scaffold_artifact(item_id, "investigation")`. Your family's sections appear; each one tells
you what it owes. Fill them as you go from here on, never at the end.

Has this item an `artifacts/brief.md`? Read it — `## Problem`, `## Context`, `## Classification`.
No brief means a standing sweep: its subject is in its own title, and **its questions are your
family's kinds, quoted from the guide.** Do not write a fresh set — a paraphrase changes what gets
swept.

Write into `## Questions`, before you read any code:

1. **The questions.** From the brief, as questions — three sharp ones beat eight vague ones. Or
   your family's kinds, quoted, plus anything the item's description adds.
2. **The walls** — what is in scope and what is NOT. "The whole repo" is not a wall: name the trees
   you exclude (generated, vendored, build output, lockfiles) and how big each is, or the exclusion
   reads later as a gap.
3. **Done** — what must be true to finish. Per kind for a sweep: inventory built, every candidate
   proved or rejected. Without it, a kind you ran out of budget for reads as clean.

**Check:** from your questions alone, can someone name what this sweep will not have looked at, and
how much of it?

A thread leading outside the walls becomes an open thread, not a detour.

## 3 · Split the surface and spawn readers

First confirm the subject is there — the path resolves, the repo is present, the URL answers. In the
same breath, settle what tools you have: one command that reports which interpreters and linters
exist and whether your item folder is writable. Record the answer and treat it as settled — a
missing tool is a line in your record, not something to rediscover later.

**Census first, then split.** If your family's guide names a mechanical pass — an inventory, a
declaration list, a file walk — run it yourself, once, into your scratch directory, before any
reader exists. Split first and every reader rebuilds it, so the sweep's most expensive command is
paid for once per reader and their counts disagree.

Then spawn one `subagent_type: superme-dev:investigator` per slice, each with `run_in_background: false`. Use that exact string: a partial
identifier resolves to a generic reader instead of erroring.

Split by question, then by SIZE — the census tells you which slices are big. Cut an outsized one by
area; keep slices within roughly 2× of each other, because an overloaded reader returns less rather
than failing, and a thin answer looks like a clean area.

> **Test:** would knowing the answer to question B change HOW you read for question A?
> No → separate readers.

Read it all yourself only when the answers chain, or the whole surface is small enough that
splitting costs more than it saves. Then write one line in `investigation.md` saying which of those
it was.

**Each brief carries four things and nothing else:**

1. **The bar** — the lines of your family's guide that say what counts as a finding, pasted in as
   text, not as a path. Name the file as well, so the reader can reach the rest.
2. **The walls**, from your `## Questions`.
3. **The one question, or the one area.**
4. **Your scratch directory as an absolute path**, and the census files in it. A reader told only
   what to find reaches for `$TMPDIR`, is refused, and never does the part that needed a file.

**Check:** does each brief make sense to someone who has read nothing else, and could a reader that
needs a file tell from it where to put one?

## 4 · Verify every receipt you keep

A reader's finding is a lead until you have seen the line yourself. Verify it with a range read at
the line you were given. Never open the file whole for this, and never re-read a reader's surface;
if you want to, the brief asked for summaries instead of receipts, and that is a note for your
report.

**Bad and good examples**
```example
✗ A reader cites loader.py:412 → you Read loader.py whole to check it.
✓ A reader cites loader.py:412 → you Read loader.py offset=395 limit=45.
```

Anything you could not verify goes to `## Open threads` marked unverified, or goes.

**Once readers are out, you stop searching.** From the first spawn onward your only searches are:

1. verifying one receipt at the line you were handed,
2. resolving a contradiction between two readers,
3. finishing a hiding-mechanism check a reader flagged and could not complete.

Enumerating the surface again, or searching for the kind of thing a reader was sent to find, is the
sweep run twice — you pay for both and learn nothing the second time. If you catch yourself doing
it, the brief was wrong: say so in `investigation.md` and spend what is left on the gap it names.

## 5 · Fill the record

- **Read only what could change an answer.** Your subject is the tree the item points at. Generated
  code, vendored dependencies, lockfiles, build output, caches and session logs cost the same per
  token as source and produce no finding — count them from a listing, never read them. Nor does a
  sweep need to understand the system to answer its questions: a PRD, an architecture doc or a
  README tour is evidence only when a question is about what the document CLAIMS.
- **A listing you produced once is a file, not a command to re-run.** Write an inventory, a
  candidate shortlist or a file census into the item folder the first time you build it, then read
  it back. Re-deriving it costs the same as building it and tells you nothing new.
- **A reader's clean area that arrives without numbers is UNSWEPT.** It belongs in `## Open
  threads`, never in the record as clean.
- **When a question asks how something behaves or how much it costs, measure it.** Write a throwaway
  script inside the item folder and run it there: `cd <item-dir> && python3 bench.py`. Name no path
  outside the folder. If you cannot measure it, say which number is missing and what it would have
  settled.
- **A source outside the repo is yours when the item or the owner names one.** `WebFetch` and
  `WebSearch` need no approval. Pin a URL the way you pin a `file:line` — the address and the date
  you read it. A doc page is evidence about the library, never about how this codebase uses it.
- **If answering needs material too big for the item folder** — a cloned repository, a bulk dataset
  — stop and say so in `## Open threads`. Where it should live is the owner's call.

## 6 · Size the follow-up work

`## Follow-up work` is items someone could file as they stand, in the order they should land, each
naming what it touches. Your family's guide says how to shape them.

Group findings that share one fix into one item.

**Bad and good examples**
```example
✗ "Improve error handling."
✓ "Validate the path argument in the three handlers at routes/files.py:88,140,203."
```

**Settle what reading can settle.** A follow-up that hangs on a question you could answer by opening
the code is not blocked — answer it and write the answer. Pass up only the calls that turn on a
preference, and say for each which way you would go; review types them and the owner rules the few
that are theirs.

**Check what the owner has already ruled.** Before you pass ANY call up, `read_decisions`. A title
that answers your call means it is settled: write the finding with the `D-NNN` cited as its answer,
and do not pass it up. The ledger is the record of questions this owner has already been asked —
re-asking one spends a decision they already made.

**You record; you never file.** The owner chooses every branch-off at the review gate.

## 7 · Write the report and bank a checkpoint

Fill the report template your trigger carries and hand the whole body to
`file_phase_report`. It owns the path and refuses a report with an unfilled slot left in it —
never write the file yourself. Then bank a `write_checkpoint`: which questions are answered, which
are open, where to pick up.

Write it plainly, in fewer words than feel natural. Never restate the item's kind, deliverable or
id. Omit a field rather than filling it with "none".

## 8 · Report the run

- **`success`** — every question is answered, or its Done criterion is met.
- **`partial`** — you ran out of run before you ran out of questions. Name the open ones.
- **`blocked`** — nothing could be investigated: the subject is unreachable, or the walls make the
  work impossible.

## Chat and completion style

Plain language, under 30 words, bullets when there is more than one point.

## Pitfalls

- **Editing code "just to try something"** — an experiment that must mutate code is a proposal.
- **Answering past the walls** — park it as an open thread.
- **Recording a conclusion instead of what you saw** — the verdicts get drawn at review, from this.
