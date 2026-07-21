---
name: triage
description: "Triage a fresh work-item: confirm its kind (implementation vs research), right-size its scope, propose its deliverable, and shape the brief the owner decides on. Use when a work-item is in its triage phase or the owner asks to triage/classify a new item; not for planning the approach (use plan) or for capturing a brand-new idea (use create-inbox-item)."
argument-hint: "work-item id (optional — defaults to the bound item)"
category: workspace
---

# Triage a work-item

Every item passes triage before real work: the kind it was created with is a PROPOSAL until this
gate confirms it. Produce the classification + brief the owner approves at the triage-exit gate.
`status`/`phase` belong to the kernel and the owner's gate — the one rule of this phase.

## 1 — Read what exists

Read `item.md` and everything in `preliminary/` (the handoff brief, if this item was branched off
or pushed with context). Skim the project's `general/project-prd.md` deliverables list. *Done
when:* you can say in one sentence what this item wants.

## 2 — Classify the kind

Propose `implementation` (changes code — gets a worktree + full vet/review pipeline) or
`research` (answers questions — read-only on code, findings instead of merges). If the intent
mixes both, propose research FIRST with a spawn follow-up for the build — a mixed item stalls at
whichever pipeline it isn't. State your call and the reason in one line.

## 3 — Right-size the scope

If the item is really several independent deliveries, say so and propose the split: keep the core
here, branch the rest off via `create_inbox_item` (relation `spawn` — they wait in the inbox for
the owner's push). Don't split what one session can plausibly deliver; the tax of extra items is
real.

## 4 — Propose the deliverable, then RECORD the classification

Deliverable — exactly one of: the parent's deliverable (a branched-off child usually inherits it) ·
an existing deliverable from the PRD · a NEW deliverable named for the owner to confirm (never
append it yourself at triage) · none (standalone chore). This anchors the item on the roadmap.

Then call `set_triage_classification(item_id, kind, deliverable)` — the kind from step 2 plus the
deliverable when it's an EXISTING PRD slug (omit it for "none"; a NEW deliverable stays a prose
proposal until the owner confirms it). Prose alone is not a record: the gate, the graph, and every
later phase read these fields from the item, not from this chat.

## 5 — Sharpen the item record

Rewrite `item.md`'s body so a cold session could start from it alone: intent, constraints you
learned, pointers into `preliminary/`. Leave the frontmatter untouched — the body is
yours, the fields are the kernel's.

## 6 — Present the gate brief

End with a SHORT brief the owner decides on — anchored in what they already know, one decision:

> **Triage: <title>** — kind `<kind>` (<one-line reason>) · deliverable `<proposal>` · scope
> <as-is | split proposed>. **Recommend:** approve → plan. <one line of stakes, if any.>

Then stop. The owner advances the phase; a request to change kind/scope is just another round here.

## Background runs

Triage fires automatically when an item is pushed (a background run — the kernel fired this
turn; see the system prompt). Do steps 1–6 end-to-end without stopping:

- **Never interview** — make the most reasonable classification call from `item.md` +
  `preliminary/`; if genuinely torn, pick the safer kind (research over a mixed item) and say why
  in the brief.
- Always call `set_triage_classification` — the `triaged_at` stamp it writes is what lifts the
  gate. A run that only presents a brief leaves the item stuck (the gate stays "no classification
  recorded").
- End with the step-6 gate brief and stop; the owner glances and approves.

## Pitfalls

- **Starting the plan** — triage decides WHAT this item is, not HOW to do it; approach talk
  belongs in the plan phase.
- **Appending a new deliverable to the PRD yourself** — at triage a new deliverable is a proposal;
  the owner confirms it first.
- **Ignoring `preliminary/`** — the handoff brief is why this item exists; re-deriving intent from
  the title alone loses the discussion that shaped it.
