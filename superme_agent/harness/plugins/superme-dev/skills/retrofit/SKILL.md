---
name: retrofit
description: "Establish SuperMe memory for an existing codebase by reverse-engineering it — spawn parallel readers to comprehend the code, clarify intent with the owner, then draft the general/ anchor docs (architecture-heavy) to an approval gate. Use when connecting a repo that already has substantial code but no project memory yet. Don't use for a new or empty repo (use project-init) or for routine edits to an already-established doc (just edit it)."
argument-hint: "(optional) an area of the codebase to start from"
category: onboarding
---

# Retrofit an existing codebase into memory

Reconstruct this project's `general/` anchor docs from the code that's already there, then hand off.
**Do not** mint work-items. Per-file authoring guides live in this plugin's `general-dev-knowledge-asset/` folder (paths
below are relative to this skill).

## Step 1 — Confirm the cold start
Read `general/` at the injected dev-knowledge root and confirm the repo has real code. If the anchor
docs already hold real content, stop: this is drift, not retrofit.

## Step 2 — Load the contract
Pull the `dev-knowledge-structure` constitution. Read a doc's guide from `../../general-dev-knowledge-asset/<doc>.md`
before you draft it.

## Step 3 — Comprehend the codebase
Spawn **parallel reader subagents**, one per subsystem, each with a self-contained prompt and an explicit
return shape. Have them report: entry points, modules and responsibilities, the stack, data flow,
external dependencies, and surprises. Merge into one comprehension map.

## Step 4 — Clarify intent with the owner (optional)
Where the code can't tell you *why* — intent, direction, priorities — interview the owner (one question
at a time, each with your recommendation) to confirm the inferred intent and fill the gaps.

## Step 5 — Draft the anchor set
Write all five docs into `general/`, each following its guide in `../../general-dev-knowledge-asset/`:
`architecture.md` **(heavy — reconstructed from the comprehension map)** · `project-prd.md` (inferred,
then clarified) · `spec.md` (the observed stack + confirmed decisions) · `roadmap.md`
(**forward-only — no history**) · `resources/index.md`. After drafting `spec.md`, call
`suggest_assets` with its text — it auto-adopts the confidently-relevant pooled assets for this repo;
note which were adopted (the owner curates them later in the dashboard).

## Step 6 — Verify against the code
Check the drafts back against the comprehension map: every architectural claim traces to code a reader
actually reported, not to inference. Flag anything you couldn't ground for the owner in step 7.

## Step 7 — Hand off
State that memory is established — Orient is valid, the board renders, and work now begins from an inbox
item.

## Pitfalls
- Serial reading — comprehend with parallel subagents scoped per subsystem, not one linear crawl.
- Recording history in the roadmap — it is forward-only; the past lives in the code and git.
- Inventing intent the code can't tell you — ask the owner instead of fabricating a rationale.
- Minting a work-item here.
