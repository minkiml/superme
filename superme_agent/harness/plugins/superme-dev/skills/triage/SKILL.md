---
name: triage
description: Triage a fresh work-item — confirm its kind (implementation vs research), right-size its scope, propose its deliverable, and author the brief the plan phase starts from. Use when a work-item is in its triage phase or the owner asks to triage or classify a new item; not for planning the approach (use plan) or for capturing a brand-new idea (use create-inbox-item).
argument-hint: "[work-item-id]"
category: workspace
---

# Triage a work-item

Decide WHAT this item is — kind, scope, deliverable — and author `artifacts/brief.md`, the doc a
cold plan session starts from. The kind it was created with is a proposal until the triage-exit
gate confirms it.

## Step 1 — Read what exists

Read `item.md` and everything in `preliminary/` (the handoff brief is why this item exists — the
title alone loses the discussion that shaped it). Skim the project's `general/project-prd.md`
deliverables list. Done when you can say in one sentence what this item wants.

## Step 2 — Classify the kind

Propose `implementation` (changes code — worktree + build⟷vet + review) or `research` (answers
questions — read-only on code, findings instead of merges). If the intent mixes both, propose
research FIRST with a spawn follow-up for the build — a mixed item stalls at whichever pipeline
it isn't. If genuinely torn in a kernel-fired run, pick the safer kind (research over a mixed
item) and say why in the brief.

## Step 3 — Right-size the scope

If the item is really several independent deliveries, keep the core here and branch the rest off
via `create_inbox_item` (relation `spawn`). Don't split what one session can plausibly deliver —
the tax of extra items is real.

## Step 4 — Record the classification

Deliverable — exactly one of: the parent's (a branched-off child usually inherits it) · an
existing PRD deliverable · a NEW one named in prose for the owner to confirm (never append it to
the PRD yourself) · none (standalone chore).

Call `set_triage_classification(item_id, kind, deliverable)` — kind from step 2, deliverable only
when it's an EXISTING PRD slug. Prose alone is not a record: the gate and every later phase read
these fields from the item, not from this chat.

## Step 5 — Author the brief

`scaffold_artifact(item_id, "brief")`, then fill its slots: the shaped ask, the classification
with reasons, and the context a cold session needs (pointers into `preliminary/` and the repo,
not copies).

## Step 6 — Write the report, then stop

Copy `../plan/templates/report-intake-template.md` (shared with plan) to
`reports/report-triage.md` and fill it as the Triage variant — every line traces to brief.md; the
template's caps are the bar. Then state your classification in one line and stop — the owner
advances the phase; a request to change kind/scope is another round here.

## Pitfalls

- **Starting the plan** — triage decides WHAT this item is, not HOW to do it.
- **Presenting without recording** — the `triaged_at` stamp from `set_triage_classification` is
  what lifts the gate; a run that only writes prose leaves the item stuck.
