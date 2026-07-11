---
name: project-init
description: "Establish a brand-new project's SuperMe memory from scratch — grill the owner, research real unknowns, and draft the general/ anchor docs (PRD · spec · roadmap · architecture) to an approval gate. Use when connecting a new or empty repo that has no project memory yet, or when the owner asks to initialize / set up / establish this project's memory. Don't use for an existing codebase with real code (use retrofit) or for routine edits to an already-established doc (just edit it)."
argument-hint: "(optional) a one-line intent for the project"
category: onboarding
---

# Initialize a project's memory

Establish this project's `general/` anchor docs, forward-looking, and hand off. **Do not** mint
work-items. Per-file authoring guides live in this plugin's `general-dev-knowledge-asset/` folder (paths below are
relative to this skill).

## Step 1 — Confirm the cold start
Read `general/` at the injected dev-knowledge root. If the anchor docs already hold real content, stop:
this is drift, not init — name the doc to edit and hand back.

## Step 2 — Load the contract
Pull the `dev-knowledge-structure` constitution (the doc set, the two-tier scaffold, the conventions).
Read a doc's guide from `../../general-dev-knowledge-asset/<doc>.md` before you draft it.

## Step 3 — Grill the owner
Relentlessly interview the owner one question at a time, each with your recommended answer, resolving
dependencies as you go.
Cover the PRD's sections (see its guide): what it is · problem & why · users · the **deliverables** (the
value chunks that become `d-` ids) · success signals · non-goals · open questions. Stop when the picture
is coherent — depth is soft.

## Step 4 — Research real unknowns
Only where an answer leaves a genuine gap (an unfamiliar stack norm, a comparable system, a hard
constraint), spawn a focused research subagent with a self-contained prompt and an explicit return shape;
fold its findings into the spec. Skip this for intent that's still just an idea.

## Step 5 — Draft the anchor set
Write all five docs into `general/`, each following its guide in `../../general-dev-knowledge-asset/`:
`project-prd.md` · `spec.md` · `roadmap.md` (forward-only) · `architecture.md` (a minimal stub) ·
`resources/index.md`. Bias forward: PRD and roadmap carry the weight; architecture grows later.
After drafting `spec.md`, call `suggest_assets` with its text — it auto-adopts the confidently-relevant
pooled assets for this repo; note which were adopted (the owner curates them later in the dashboard).

## Step 6 — Approval gate
Present the draft set, iterate with the owner, and converge. Nothing is established until they approve;
a thin-but-real set passes.

## Step 7 — Hand off
State that memory is established — Orient is valid, the board renders, and work now begins from an inbox
item.

## Pitfalls
- Minting a work-item here — onboarding commits docs + roadmap scaffold only.
- Writing history into the roadmap — it is forward-only.
- Interrogating what you could research, or researching what only the owner can answer (intent/direction).
