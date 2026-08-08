---
name: retrofit
description: Establish SuperMe memory for an existing codebase by reverse-engineering it — spawn parallel readers to comprehend the code, clarify intent with the owner, then draft the general/ anchor docs (architecture-heavy) to an approval gate. Use when connecting a repo that already has substantial code but no project memory yet. Don't use for a new or empty repo (use project-init) or for routine edits to an already-established doc (just edit it).
argument-hint: "[starting-area]"
category: onboarding
---

# Retrofit an existing codebase into memory

Reconstruct this project's `general/` anchor docs from the code that's already there; then — only with
the owner's explicit go — **launch** the near-term deliverables as a cohort of autopilot work-items.
Docs first and always; work-items only after Step 6's verify and the owner's launch confirm.
Per-file authoring guides live in this plugin's `general-dev-knowledge-asset/` folder (paths
below are relative to this skill).

## Step 1: Confirm the cold start
Read `general/` at the injected dev-knowledge root and confirm the repo has real code. If the anchor
docs already hold real content, stop: this is drift, not retrofit.

## Step 2: Load the contract
Pull the `dev-knowledge-structure` constitution. Read a doc's guide from `../../general-dev-knowledge-asset/<doc>.md`
before you draft it.

## Step 3: Comprehend the codebase
Spawn **parallel reader subagents** (model: sonnet), one per subsystem, each with a self-contained prompt
and an explicit return shape. Have them report: entry points, modules and responsibilities, the stack, data flow,
external dependencies, and surprises. Merge into one comprehension map.

## Step 4: Clarify intent with the owner
Where the code can't tell you *why* — intent, direction, priorities — interview the owner, one question
at a time, to confirm the inferred intent and fill the gaps.

**Always ask, even if the code left you confident.** Intent is the one thing code cannot supply, and a
confident agent that skips this writes the PRD from inferred intent and never finds out it guessed
wrong. Short is fine — two questions beats none.

**Every question carries a recommendation, and a recommendation is useless bare.** For any question
that shapes a deliverable, the data model, or what's next, lay it out as a LIST — one labelled
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

## Step 5: Draft the anchor set
Write the docs into `general/`, each following its guide in `../../general-dev-knowledge-asset/`:
`architecture.md` **(heavy — reconstructed from the comprehension map; the observed stack, the
invariants the code actually holds to, and what it conspicuously refuses to do)** · `capabilities.md`
(**what the code demonstrably does TODAY — present tense only; mark anything you can't verify
`[TBC — what you observed and why you're unsure]` rather than guessing**) · `project-prd.md` (inferred,
then clarified) · `decisions.md` (only choices you can actually ground — a retrofit reconstructs few;
an empty ledger is honest, an invented one is not) · `roadmap.md` (**forward-only — no history**) ·
`resources/index.md`. `verification.md` is **not yours to write** — it holds checks SuperMe has
actually run and seen pass here, and a repo being retrofitted has none yet however good its existing
test suite is; note the suite in `architecture.md` instead. After drafting `architecture.md`, call
`suggest_assets` with its text — it auto-adopts the confidently-relevant pooled assets for this repo;
note which were adopted (the owner curates them later in the dashboard).

## Step 6: Verify against the code
Check the drafts back against the comprehension map: every architectural claim traces to code a reader
actually reported, not to inference. Flag anything you couldn't ground for the owner in step 7.

## Step 7: Propose the launch, then launch it
Memory is established. Offer to put the near-term deliverables into motion as a **launch cohort** —
work-items that run themselves through triage → plan → build⟷vet → review on autopilot, no human until
a review gate.

1. **Draw the cohort from the PRD**, not the whole roadmap: one work-item per deliverable ready to
   start now (skip anything gated on an open decision or clearly later-phase). `after` edges come from
   each deliverable's `Needs`. On an existing codebase these items are typically the next enhancements
   or fixes — scoped against what the code already does, per your comprehension map.
2. **Present the list once** — titles + who-waits-on-whom + roughly what each delivers — and get the
   owner's **single explicit confirm**. If they'd rather establish memory and drive work by hand, skip
   to Step 8 and launch nothing.
3. On confirm, call **`itemize_and_launch`** with the batch: `key` = the deliverable id, `title`, a
   one-line `description` of the value (not a plan), and `after` from the `Needs`.

## Step 8: Close out
State what is now **in motion**, **when the owner is next needed**, and the **one place to watch**
(Pipeline) — read it off the tool's result. If nothing was launched, hand off plainly: memory is
established, Orient is valid, the board renders, and work begins from an inbox item when the owner is
ready.

## Chat response style
- Use plain and easy language.
- Keep your response short, clear, and to the point.
- Use bullets or numbered lists to organize information if there is more than one point.

## Pitfalls
- Serial reading — comprehend with parallel subagents scoped per subsystem, not one linear crawl.
- Recording history in the roadmap — it is forward-only; the past lives in the code and git.
- Inventing intent the code can't tell you — ask the owner instead of fabricating a rationale.
- Launching before Step 6's verify or without the owner's explicit launch confirm — docs first, cohort opt-in.
- Itemizing the whole roadmap — launch only what's ready now.
