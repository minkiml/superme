---
name: investigate
description: "Run a research work-item's investigation: answer the plan's questions inside its boundaries, read-only on code, notes in the item folder. Use when a research-kind work-item is in its investigate phase; not for implementation items (use build) or for writing up conclusions (use report)."
argument-hint: "work-item id (optional — defaults to the bound item)"
category: workspace
---

# Investigate (research item)

Answer the plan's **Questions**, within its **Boundaries**, to its **Done criteria**. Research
items read the main tree directly and change NOTHING in it — no worktree exists for them; your
only writes are the item's own folder.

## Step 1: The plan bounds the work

- Read `artifacts/plan.md`: Questions are the deliverable, Boundaries are hard walls (time, depth,
which subsystems), Done criteria say when to stop.
- An investigation without walls doesn't converge — when a thread leads outside the boundaries, note
it as a follow-up instead of chasing it.

## Step 2: Investigate with receipts

- Work question by question — and when questions are independent, **fan them out**: spawn parallel
Explore subagents, one per question (or per subsystem for a broad question), each returning evidence
with `file:line` pointers, not summaries. You stay the synthesizer: cross-check what comes back before
recording it — subagent findings are leads until you've seen the receipt. A sequential read-through is
right only when the questions genuinely build on each other.
- Keep running notes in the item folder (e.g. `artifacts/notes.md` — free-form, yours): evidence with
`file:line` pointers, sources, measurements, dead ends. A finding you can't point back to a source is
an opinion; the report phase can only be as grounded as these notes.

## Step 3: Ideas become items, not detours

- Implementation ideas the research surfaces → `create_inbox_item` (your item as `spawned_from_item`,
relation `spawn`) with the context in the brief.
- The research item itself never starts building — that's a kind violation, and it's why research
items don't get a worktree.

## Step 4: End of session

- Bank a checkpoint (`write_checkpoint`): which questions are answered vs open, what the evidence
leans toward, where to pick up.
- When every question is answered (or its Done criterion met), say the investigation is complete and
stop — writing findings up is the report phase, and the owner advances phases.

## Pitfalls

- **Editing code "just to try something"** — research is read-only on the repo by contract;
  a needed experiment that mutates code is an implementation spawn.
- **Answering beyond the boundaries** — thoroughness outside the walls is scope creep with a
  better reputation; park it as a follow-up.
