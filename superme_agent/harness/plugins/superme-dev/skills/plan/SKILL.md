---
name: plan
description: "Plan a work-item: turn its intent into an approach, a task checklist, and the gate feeds (change map, behavior preview, assumptions) for owner review. Use when a work-item is in its plan phase or the owner asks to plan/design an item; not for classifying a fresh item (use triage) or for implementing an approved plan (use build)."
argument-hint: "work-item id (optional — defaults to the item in scope)"
category: workspace
---

# Plan a work-item

Take a `plan`-phase work-item and produce its plan/design for owner review and the build phase.
**Plan only:** your writes are the item's own artifacts; `status`/`phase` belong to the kernel
and the owner's gate — the one rule of this phase.

Your plan is read twice, and both readings must hold: **build executes it** (tasks + checks) and
**the owner judges it at the gate in ~2 minutes without reading code** — from the change map,
the predicted before/after, and your assumption list. A plan whose gate feeds are vague wastes
the one cheap moment owner attention is on it; everything downstream (bad builds, extra
build⟷vet cycles) inherits that.

Outputs: `artifacts/plan.md` (always) · `artifacts/gate-report-plan.html` (always — step 5) ·
`artifacts/prd.md` (`type: prd`, only for complex/ambiguous items: problem · key decisions ·
out of scope).

## Step 1: Locate the item

- Resolve `work-items/<id>/` from the argument, the bound session, or the owner's message. Read
`item.md` (intent + frontmatter) and skim `artifacts/`. For prior activity, call `read_dev_log` with
this `item_id`.
- If the item is **not** in `plan`, stop — it's past the planning gate; say so.
- *Done when:* you understand the item's intent.

## Step 2: Size it up — recon before design

Pull what the item needs: relevant code, the dev-knowledge contract (pull the
`dev-knowledge-structure` constitution) if unsure, linked items. Then:

- **Straightforward** (clear intent, contained change, one obvious design) → go straight to the plan;
  don't interrogate over an easy task.
- **Complex / ambiguous** (broad blast radius, unclear requirements, several viable designs) →
  **fan out recon first**: spawn parallel Explore subagents, one per question of the form "map
  how X works today" (entry points, data flow, the components a change would touch) — their
  answers are what make `## Touches` real instead of guessed. Then run a **focused interview**:
  only questions that change the design, one at a time, each with your recommendation; stop the
  moment the plan is sound. Record the outcome in `artifacts/prd.md`.

*Done when:* you know the design, which components it touches, and whether a `prd.md` is warranted.

## Step 3: Draft the plan — the gate feeds are the plan

Scaffold first — `scaffold_artifact(item_id, "plan")` writes the skeleton — then fill its
`<fill:…>` slots. The section-by-section field spec is code-owned: see
`../../references/artifacts.md` § "plan.md — the section contract". What each section must
*achieve*:

- **`## Approach`** — sharp, not exhaustive; every part of it maps to a task.
- **`## Touches`** — the change map's data. List every component you'll create (`new`), edit
  (`modify`), or rely on unchanged (`read`). If recon didn't tell you what a row should be, that
  row is a question for step 2, not a guess.
- **`## Behavior preview`** — pick the ONE observable surface the owner would check with their
  own eyes (a command's output, a screen, an API response) and write it twice: as it is today,
  and as you predict it after the plan lands. The prediction is a commitment — review compares
  the real capture against it. If you can't write the after-pane, you don't know what you're
  building yet.
- **`## Tasks`** — ordered `- [ ]` items, each small enough to verify on its own; the to-do
  list build ticks. Tasks live INSIDE plan.md — there is no separate tasks doc.
- **`## Risks & assumptions`** — every call you made that the owner didn't: defaults chosen,
  scope edges, compatibility bets. One line each, concrete enough to veto. This list is what
  the owner actually reviews — burying an assumption here is honest; omitting it is not.

Interactive: propose and iterate with the owner until sound before recording. *Done when:* every
slot is filled and the approach, touches, preview, and tasks tell one consistent story.

## Step 4: Write the checks — this is where builds earn their autonomy

Plan writes BOTH check lists, because this gate is the one cheap moment the owner's eyes are on
the criteria — build must never author its own exam. Field spec: the reference above.

**`## Inner checks`** — commands whose EXIT CODE decides them; if a line needs an opinion to
judge, it belongs in the vet plan.

**`## Vet plan`** — the contract a FRESH vet agent with zero context executes. The judgment
that matters is falsifiability of each `expect`: ✅ "list output has exactly one row reading
12.50 / groceries / lunch" · ❌ "the add command works correctly". If you can't picture the
output that fails it, rewrite it.

The bar for `depth: none` is high — NOT "it's only a rename" or "it's only frontend". If there is
an observable surface (a screen, a CLI, an endpoint), there is a vet. `none` is for items with no
observable surface at all.

*Done when:* `plan.md` has no unfilled slots and `scaffold_artifact`'s gate check would pass —
the pre-main gate mechanically rejects a structurally broken vet plan or touches yaml.

## Step 5: Render the gate report

- Fill `templates/gate-report.html` (in this skill's folder): replace its single `{{DATA_JSON}}`
slot with a one-line JSON object built from the plan you just wrote — the schema is documented at
the top of the template. Everything in it restates plan.md; invent nothing new. Write the result to
`artifacts/gate-report-plan.html`. The gate check fails on any leftover `{{…}}` slot.
- *Done when:* the file exists and opens as a rendered report.

## Step 6: Record it

- Append the artifact **path strings** to `item.md`'s `artifacts` list — e.g.
`artifacts: [artifacts/plan.md, artifacts/gate-report-plan.html]` (add `artifacts/prd.md` if
written); each markdown file's own frontmatter carries its `type`.
- *Done when:* `item.md` lists the artifacts.

## Step 7: Hand the gate back

- State the plan and tasks are recorded and the owner can review them and advance the phase
(`plan → build`) when ready. Do not self-advance.

## Background runs

On a background run (the "Plan it" quick-action or the approve→plan auto-fire — the kernel fired
this turn; see the system prompt), do steps 1–6 end-to-end without stopping:

- **Never interview** — make the most reasonable assumption and record it under
  **`## Risks & assumptions`** (that section is exactly the channel for calls made without the
  owner); write `prd.md` only if the item is genuinely complex.
- Recon fan-out still applies to complex items; skip step 3's conversational iteration — go
  straight to a sound first draft.
- Still produce the complete `plan.md` AND `gate-report-plan.html` — on a background run the
  report is the owner's first look at your plan.

## Common Pitfalls

1. **A checklist outside `## Tasks`** — the interface reads progress from plan.md's `## Tasks`
   section only; a stray tasks file is invisible.
2. **A guessed `## Touches` row** — the change map is the owner's blast-radius view; a wrong row
   misleads the exact decision the gate exists for. Recon first.
3. **A described (not shown) behavior preview** — "the list will show the new entry" is prose;
   the pane wants the output itself, line for line.
4. **Interrogating an easy task** — interview only when the design genuinely forks.
5. **prd.md for simple work** — `plan.md` alone is the default; `prd.md` is the exception.
6. **A vague `expect`** — "works correctly" / "looks right" give the vet agent nothing to
   falsify; the gate brief flags them to the owner.
7. **An inner check that needs judgment** — if its verdict isn't an exit code, it belongs in the
   vet plan, not `## Inner checks`.
8. **Dict artifact entries** — `item.md` artifacts are plain path strings; `type` lives in each artifact's frontmatter.
9. **A task build can't do itself** — build can edit code in its worktree and STAGE contract-doc
   changes (`stage_knowledge_delta`, applied at close), but it can't perform work that needs an
   owner decision (which name is the public one, whether to re-scope a deliverable) or reach outside
   its boundary. Don't hand build a task walled off that way and expect it to grind through it;
   settle the decision now (record it under `## Risks & assumptions` for the gate) so build gets a
   task it can actually complete. If build later hits an unforeseen wall it records the gap and
   review decides — but a KNOWN wall belongs to the plan gate, not a mid-build surprise.
