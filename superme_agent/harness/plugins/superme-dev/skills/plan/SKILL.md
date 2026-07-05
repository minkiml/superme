---
name: plan
description: "Plan a work-item: turn its intent into an approach and a task checklist for owner review."
argument-hint: "work-item id (optional — defaults to the item in scope)"
category: workspace 
---

# Plan a work-item

Take a `plan_design` work-item and produce its plan/design for owner review and the build phase. You
are in dev mode, anchored to dev-knowledge. **Plan only:** read anything, but the only files you write
are the item's own artifacts, and you never touch `status`/`phase` — the system owns run-state, and
advancing the phase is the owner's gate. Produce two artifacts always, a third only when the work earns it:

- `artifacts/plan.md` (`type: plan`) — the **approach**: how you'll build it, the decisions, the risks.
- `artifacts/tasks.md` (`type: tasks`) — the **checklist** of concrete sub-tasks as `- [ ]` items.
- `artifacts/prd.md` (`type: prd`) — **only for complex / ambiguous items**: problem · key decisions · out of scope.

## 1. Locate the item

Resolve `work-items/<id>/` from the argument, the bound session, or the owner's message. Read `item.md`
(intent + frontmatter) and skim `artifacts/`. For prior activity, call `dev_log` with this `item_id`.
If the item is **not** in `plan_design`, stop — it's past the planning gate; say so. *Done when:* you
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

Write `artifacts/plan.md`: **Approach** (a few sentences) · **Key decisions** (each with your
recommendation) · **Risks / unknowns** (what could bite, what needs the owner). Interactive: propose and
iterate with the owner until sound before recording. Sharp, not exhaustive. *Done when:* `plan.md`
states a sound approach.

## 4. Write the checklist

Write `artifacts/tasks.md`: ordered `- [ ] <task>` items, each small enough to verify on its own — the
to-do list the build phase ticks (`- [x]`). Keep it here, not inside `plan.md`. *Done when:* every part
of the approach maps to a task.

## 5. Record it

Append the artifact **path strings** to `item.md`'s `artifacts` list — e.g.
`artifacts: [artifacts/plan.md, artifacts/tasks.md]` (add `artifacts/prd.md` if written); each file's
own frontmatter carries its `type`. Do **not** touch `status`/`phase`; the system writes the
`plan.start`/`plan.end` LOG events. *Done when:* `item.md` lists the artifacts and `status`/`phase` are
untouched.

## 6. Hand the gate back

State the plan and tasks are recorded and the owner can review them and advance the phase
(`plan_design → build_eval`) when ready. Do not self-advance.

## Autonomous (headless) runs

When run autonomously (the "Plan it" button — no human in chat), do steps 1–5 end-to-end without stopping:

- **Never interview** — make the most reasonable assumption and record it under **Risks / unknowns** in
  `plan.md`; write `prd.md` only if the item is genuinely complex.
- Skip step 3's conversational iteration — go straight to a sound first draft.
- Still produce **both** `plan.md` and `tasks.md`; still don't touch `status`/`phase`. Writes are
  sandboxed to the item folder.

## Common Pitfalls

1. **Touching status/phase** — the system owns run-state, the owner owns the gate; only write artifacts.
2. **Logging the run yourself** — the daemon writes `plan.start`/`plan.end`.
3. **Burying the checklist in plan.md** — tasks live in `tasks.md` so the interface reads progress.
4. **Interrogating an easy task** — interview only when the design genuinely forks.
5. **prd.md for simple work** — two artifacts is the default; `prd.md` is the exception.
6. **Dict artifact entries** — `item.md` artifacts are plain path strings; `type` lives in each artifact's frontmatter.
