---
name: triage
description: Triage a fresh work-item — confirm its kind (implementation vs research), right-size its scope, propose its deliverable, and author the brief the plan phase starts from. Use when a work-item is in its triage phase or the owner asks to triage or classify a new item; not for planning the approach or for capturing a brand-new idea.
argument-hint: "[work-item-id]"
category: workspace
---

# Triage a work-item

Decide WHAT this item is — kind, scope, deliverable — and author `artifacts/brief.md`, the doc a cold plan session starts from. The kind it was created with is a proposal until the triage-exit gate confirms it.

## Step 1 — Read what exists

Read `item.md` and everything in `preliminary/` (the handoff brief is why this item exists — the title alone loses the discussion that shaped it). Skim the project's `general/project-prd.md` deliverables list. Done when you can say in one sentence what this item wants.

## Step 2 — Classify the kind

- Propose `implementation` (changes code works; e.g., bug fix, new feature, improvements, so on) or `research` ( dedicated deep investigation & exploration works on a request topic; e.g., deep audit (security, broken features, optimizations, so on), learning from other codebases, bug findings, etc.). 
- If the intent mixes both, propose
research FIRST with a spawn follow-up for the build — a mixed item stalls at whichever pipeline it isn't. 
- If genuinely torn in a kernel-fired run, pick the safer kind (research over a mixed item) and say why in the brief.

## Step 3 — Right-size the scope

If the item is really several independent deliveries, keep the core here and branch the rest off via `create_inbox_item` (relation `spawn`). Don't split what one session can plausibly deliver — the tax of extra items is real.

## Step 4 — Name it, and record the classification

Deliverable — exactly one of: the parent's (a branched-off child usually inherits it) · an existing PRD deliverable · a NEW one named in prose for the owner to confirm (never append it to the PRD yourself) · none (standalone chore).

Title — the line the board shows. A few words naming the change, under 60 characters, no closing period: "Add dark mode support", not "I keep working at night and the white background hurts…". You are the first reader who has seen the whole ask, so this is where a weak name gets fixed — an owner capturing a ticket in a hurry often pastes the request itself, and the item was born holding its first sentence. Pass the existing title back when it already reads well.

Call `set_triage_classification(item_id, title, kind, deliverable)` — kind from step 2, deliverable only when it's an EXISTING PRD slug. Prose alone is not a record: the gate and every later phase read these fields from the item, not from this chat.

## Step 5 — Author the brief

`scaffold_artifact(item_id, "brief")`, then fill its slots: the PROBLEM, the classification with reasons, and the context a cold session needs (pointers into `preliminary/` and the repo, not copies).

`## Problem` states what is wrong today, never the fix. A capture usually arrives already solution-shaped ("add a `--date` flag") — your job is to name the symptom underneath it ("every expense is stamped today, so a late-entered receipt falls in the wrong month"). Writing the fix here spends the plan phase's whole job before it starts: a plan handed an answer implements it, and can never find the better one.

## Step 6 — Write a quick user-facing report

- Copy `templates/report-triage-template.md` to `reports/report-triage.md` and fill it — every line traces to brief.md; the template's caps are the bar. Its `<fill:…>` slots and its `<!-- … -->` notes are both instructions to you: replace the slots, drop the notes. Neither belongs in the file you write. 
- This report is for the OWNER to read and correct the shaping, not for the kernel — the kernel reads brief.md. The scope table is the point of it: in/out is what the owner accepts or sends back at this gate.
- `**Workflow:**` must name the SAME kind you recorded in step 4. The drilldown reads the kind off the item (it is what selects the machinery); this line is for whoever reads the brief on its own. A line that disagrees with the record is the one thing this field can get wrong.
- `## From you` is theirs, not yours. Copy the heading and its two empty labels through exactly as they stand and write nothing under them — the drilldown's editor is the section's only writer, and the plan phase treats whatever the owner puts there as authority. Filling it for them would hand plan an instruction nobody gave.

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

- **Starting the plan** — triage decides WHAT this item is, not HOW to do it.
- **Presenting without recording** — the `triaged_at` stamp from `set_triage_classification` is
  what lifts the gate; a run that only writes prose leaves the item stuck.
- when writing to docs, Do not include the comments part `<!-- ... -->` in the scaffold you file — it is instructions for you.