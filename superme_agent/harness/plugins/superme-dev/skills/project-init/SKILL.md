---
name: project-init
description: Establish a brand-new project's SuperMe memory from scratch — research the knowable, grill the owner, and draft the general/ anchor docs (PRD · architecture · roadmap · decisions) to an approval gate. Use when connecting a new or empty repo that has no project memory yet, or when the owner asks to initialize / set up / establish this project's memory. Don't use for an existing codebase with real code (use retrofit) or for routine edits to an already-established doc (just edit it).
argument-hint: "[project-intent]"
category: onboarding
---

# Initialize a project's memory

Establish this project's `general/` anchor docs, forward-looking; then — only with the owner's
explicit go — **launch** the first deliverables as a cohort of autopilot work-items. Docs are
minted first and always; work-items only after Step 6's approval and the owner's launch confirm.
Per-file authoring guides live in this plugin's `general-dev-knowledge-asset/` folder (paths below
are relative to this skill).

## Step 1: Confirm the cold start
Read `general/` at the injected dev-knowledge root. If the anchor docs already hold real content, stop:
this is drift, not init — name the doc to edit and hand back.

## Step 2: Load the contract
Pull the `dev-knowledge-structure` constitution (the doc set, the two-tier scaffold, the conventions).
Read a doc's guide from `../../general-dev-knowledge-asset/<doc>.md` before you draft it.

## Step 3: Pre-study, then report back
Before you ask anything, learn what you could have looked up. Spawn focused research subagents (model:
sonnet; one per question, self-contained prompt, explicit return shape) on what is knowable **from outside**: stack norms
and conventions, the plumbing available, data, comparable systems and what they got right, hard constraints.

**Scope boundary — do not research product intent.** What this is for, who it's for, the first
milestone, what "good" means: those are the owner's to state, and arriving with a pre-formed opinion
anchors them to your guess instead of surfacing theirs.

Then **report before you question** — a short "here's what I found · here's what I still need from you."
Cheap, and it lets the owner correct a wrong premise before it drives twenty questions.

## Step 4: Grill the owner
Relentlessly interview the owner one question at a time, resolving dependencies as you go.
Cover the PRD's sections (see its guide): what it is · who it's for · **why it exists** (the real
reason, not the pitch) · goals now vs direction · the **deliverables** (the value chunks that become
`d-` ids) · success signals · non-goals. Stop when the picture is coherent — depth is soft.

**Every question carries a recommendation, and a recommendation is useless bare.** For any question
that shapes a deliverable, the data model, or the first milestone, lay it out as a LIST — one labelled
line each, never run together in a paragraph:

```markdown
### <the question, as a question>
- Recommend — <the answer>
- Why — <one line>
- Instead — <alternative>, if <when you'd pick it>
- Instead — <alternative>, if <when you'd pick it>
```

Rules that keep it readable:
- **One line per label, never `·`-joined.** A single paragraph carrying three labels reads as a wall;
  the owner has to parse it before they can decide.
- **Go easy on bold.** The labels are already structure — bolding the values too turns the block into
  noise where nothing stands out because everything does.
- **Cap it.** Only consequential questions get the full shape; cheap ones stay plain questions.
  Twenty questions × six lines costs the owner more than it saves.

Question the pre-study too: if Step 3 turned up a convention that conflicts with what the owner wants,
say so now rather than quietly following one of them.

## Step 5: Draft the anchor set
Write the docs into `general/`, each following its guide in `../../general-dev-knowledge-asset/`:
`project-prd.md` (identity · goals · non-goals · deliverables as value) · `architecture.md` (the
intended stack, the invariants you're committing to, and what you're deliberately not building) ·
`decisions.md` (the D-NNN ledger — one entry per choice the interview actually settled, none for
what's still open) · `roadmap.md` (forward-only) · `capabilities.md` (**empty — nothing has shipped;
say so in one line and do not pad it with the plan**) · `resources/index.md`. Bias forward: PRD and
roadmap carry the weight; architecture's components and flows grow as code lands.
After drafting `architecture.md`, call `suggest_assets` with its text — it auto-adopts the confidently-relevant
pooled assets for this repo; note which were adopted (the owner curates them later in the dashboard).

## Step 6: Approval gate
Present the draft set, iterate with the owner, and converge. Nothing is established until they approve;
a thin-but-real set passes.

## Step 7: Propose the launch, then launch it
Memory is now established. Offer to put the near-term deliverables into motion as a **launch cohort** —
a set of work-items that run themselves through triage → plan → build⟷vet → review on autopilot, with
no human until a review gate.

1. **Draw the cohort from the PRD**, not the whole roadmap: one work-item per deliverable that is
   ready to start now (skip anything gated on a decision still open, or clearly later-phase). Each
   item's `after` edges come straight from that deliverable's `Needs` — the dependency graph the PRD
   already declares becomes the run order for free.
2. **Present the list once** — titles + who-waits-on-whom + roughly what each delivers — and get the
   owner's **single explicit confirm**. This is the one gate; after it, they're free until a review.
   If the owner would rather just establish and start work by hand, that's fine — skip to Step 8 and
   launch nothing.
3. On confirm, call **`itemize_and_launch`** with the batch: `key` = the deliverable id (so `after`
   edges wire by key), `title`, a one-line `description` of the value (not a plan), and `after` from
   the `Needs`. The tool creates them all on autopilot, parks dependents at `awaiting_upstream`, and
   starts the ready ones.

## Step 8: Close out
State what is now **in motion** (not merely what was created), **when the owner is next needed**, and
the **one place to watch** — the failure mode this replaces is a chat that just stops. Read it off the
tool's result:

> **Launched.** 4 items from 4 deliverables · **1 running now** (CLI foundation) · **3 waiting** on it ·
> first review gate expected when `d-cli` lands. Watch it in **Pipeline**; nothing needs you until an
> item reaches review.

If nothing was launched, hand off plainly: memory is established, Orient is valid, the board renders,
and work begins from an inbox item whenever the owner is ready.

## Pitfalls
- Launching before Step 6's approval, or without the owner's explicit launch confirm — docs first, and
  the cohort is opt-in.
- Itemizing the whole roadmap — launch only what's ready now; later deliverables become items later.
- A close-out that lists what was created instead of what's in motion + when you're next needed.
- Writing history into the roadmap — it is forward-only.
- Interrogating what you could research, or researching what only the owner can answer (intent/direction).
