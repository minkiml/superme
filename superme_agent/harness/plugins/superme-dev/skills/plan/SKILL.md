---
name: plan
description: "Plan a work-item: turn its intent into an approach and a task checklist for owner review. Use when a work-item is in its plan phase or the owner asks to plan/design an item; not for classifying a fresh item (use triage) or for implementing an approved plan (use build)."
argument-hint: "work-item id (optional — defaults to the item in scope)"
category: workspace
---

# Plan a work-item

Take a `plan`-phase work-item and produce its plan/design for owner review and the build phase.
**Plan only:** read anything, but the only files you write
are the item's own artifacts, and you never touch `status`/`phase` — the system owns run-state, and
advancing the phase is the owner's gate. Produce one artifact always, a second only when the work earns it:

- `artifacts/plan.md` (`type: plan`) — the **approach** + validation criteria + the **`## Tasks`
  checklist** of concrete sub-tasks as `- [ ]` items (the single task tracker — progress is derived
  from its checkbox ratio; no separate tasks doc). Scaffolded via `scaffold_artifact`.
- `artifacts/prd.md` (`type: prd`) — **only for complex / ambiguous items**: problem · key decisions · out of scope.

## 1. Locate the item

Resolve `work-items/<id>/` from the argument, the bound session, or the owner's message. Read `item.md`
(intent + frontmatter) and skim `artifacts/`. For prior activity, call `read_dev_log` with this `item_id`.
If the item is **not** in `plan`, stop — it's past the planning gate; say so. *Done when:* you
understand the item's intent.

## 2. Size it up — interview only if it earns it

Pull what the item needs: relevant code, the dev-knowledge contract (pull the
`dev-knowledge-structure` constitution) if unsure, linked items. Then:

- **Straightforward** (clear intent, contained change, one obvious design) → go straight to the plan;
  don't interrogate over an easy task.
- **Complex / ambiguous** (broad blast radius, unclear requirements, several viable designs) → run a
  **focused interview**: only questions that change the design, one at a time, each with your
  recommendation; stop the moment the plan is sound. Record the outcome in `artifacts/prd.md`.

*Done when:* you know the design and whether a `prd.md` is warranted.

## 3. Draft the plan

Scaffold first — `scaffold_artifact(item_id, "plan")` writes the skeleton (structure is
code-owned; see `../../references/artifacts.md`) — then fill its `<fill:…>` slots: the approach,
the validation criteria, and the **`## Tasks` checklist** (ordered `- [ ] <task>` items, each
small enough to verify on its own — the to-do list the build phase ticks; tasks live INSIDE
plan.md, there is no separate tasks doc). Interactive: propose and iterate with the owner until
sound before recording. Sharp, not exhaustive. *Done when:* `plan.md` has no unfilled slots and
every part of the approach maps to a task.

## 4. Record it

Append the artifact **path strings** to `item.md`'s `artifacts` list — e.g.
`artifacts: [artifacts/plan.md]` (add `artifacts/prd.md` if written); each file's
own frontmatter carries its `type`. Do **not** touch `status`/`phase`; the system writes the
`plan.start`/`plan.end` LOG events. *Done when:* `item.md` lists the artifacts and `status`/`phase` are
untouched.

## 5. Hand the gate back

State the plan and tasks are recorded and the owner can review them and advance the phase
(`plan → build`) when ready. Do not self-advance.

## Autonomous (headless) runs

When run autonomously (the "Plan it" button — no human in chat), do steps 1–4 end-to-end without stopping:

- **Never interview** — make the most reasonable assumption and record it under **Risks / unknowns** in
  `plan.md`; write `prd.md` only if the item is genuinely complex.
- Skip step 3's conversational iteration — go straight to a sound first draft.
- Still produce a complete `plan.md` (approach + validation criteria + `## Tasks`); still don't
  touch `status`/`phase`. Writes are sandboxed to the item folder.

## Common Pitfalls

1. **Touching status/phase** — the system owns run-state, the owner owns the gate; only write artifacts.
2. **A checklist outside `## Tasks`** — the interface reads progress from plan.md's `## Tasks`
   section only; a stray tasks file is invisible.
3. **Interrogating an easy task** — interview only when the design genuinely forks.
4. **prd.md for simple work** — `plan.md` alone is the default; `prd.md` is the exception.
6. **Dict artifact entries** — `item.md` artifacts are plain path strings; `type` lives in each artifact's frontmatter.
