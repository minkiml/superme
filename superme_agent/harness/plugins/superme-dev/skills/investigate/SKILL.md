---
name: investigate
description: Run a research work-item's investigation — answer the plan's questions inside its boundaries, read-only on the repo, evidence recorded in the item folder. Use when a research work-item is in its investigate phase; not for implementing anything (use build) or for drawing the conclusions (use review, at the review phase).
argument-hint: "[work-item-id]"
category: workspace
---

# Investigate (research item)

Answer the plan's **Questions**, within its **Boundaries**, to its **Done criteria**. You read the
main tree directly and change NOTHING in it — a research item has no worktree; your only writes are
the item's own folder.

## Step 1: The plan bounds the work

Read `artifacts/plan.md`: Questions are the deliverable, Boundaries are hard walls (time, depth,
which subsystems), Done criteria say when to stop. An investigation without walls doesn't converge —
a thread leading outside them is recorded as an open thread, never chased.

## Step 2: Investigate with receipts

Work question by question — and when questions are independent, **fan them out**: parallel Explore
subagents (model: sonnet), one per question (or per subsystem for a broad one), each returning
evidence with `file:line` pointers, not summaries. You stay the synthesizer: cross-check what comes
back before recording it — a subagent's finding is a lead until you have seen the receipt. Read
sequentially only when the questions genuinely build on each other.

When a question asks how something BEHAVES or how much it COSTS, measure it — reading the source
tells you the complexity class, never the number. You can run throwaway scripts, but only scoped
into your own item folder: write the script there and run it as
`cd <item-dir> && python3 bench.py` (an unscoped command at the repo cwd is denied — read-only on
the repo is the contract, and it holds for the shell too). Name no path outside the item folder;
seed the fixtures it needs there too. If a measurement genuinely cannot be made, say which number
is missing and what it would have settled — a stated gap is evidence; a guess wearing a number's
clothes is not.

`scaffold_artifact(item_id, "investigation")`, then keep it current as you go. It is the record the
final report is written from, so a claim you cannot point at does not belong in it — and the dead
ends matter as much as the answers, since they are what stops the next investigation re-walking
this one.

## Step 3: Ideas are recorded, never chased

An implementation idea the research surfaces goes into `## Open threads` — not into a new item, and
not into code. Every branch-off from a research item is decided in ONE place: the owner, at the
review gate, from the final report's proposals. Filing work here would put it on the board before
anyone chose it.

## Step 4: End of session

- Bank a checkpoint (`write_checkpoint`): which questions are answered vs open, what the evidence
leans toward, where to pick up.
- Rewrite `reports/report-investigate.md` from `templates/report-investigate-template.md` (this
skill's folder), overwriting in place — the standing answer to "where has this got to". It reports
the state of the search; the conclusions belong to the review-entry report.
- When every question is answered (or its Done criterion met), say so in one line and stop.

## Pitfalls

- **Editing code "just to try something"** — research is read-only on the repo by contract; an
  experiment that must mutate code is a proposal, not a detour.
- **Answering beyond the boundaries** — thoroughness outside the walls is scope creep with a better
  reputation; park it as an open thread.
- **Recording a conclusion instead of what you saw** — this doc holds evidence; the verdicts are
  drawn later, from it.
