---
name: review
description: "Make a vetted work-item mergeable and draft its readiness report: freshness-sync from main, tidy commits, write readiness.md for the owner's merge decision. Use when a work-item is in its review phase; not for running the vet checks themselves (use vet) — and the merge itself is the owner's action, never yours."
argument-hint: "work-item id (optional — defaults to the bound item)"
category: workspace
---

# Present a work-item for review

Get the item's branch into a state the owner can merge on a report alone — they decide on
`readiness.md`, never on a diff. You prepare; the merge button is theirs.

## 1 — Freshness first

Run `sync_from_main` (commit any loose work first). If it merged trunk changes in, the evidence
ledger just went stale — re-run the vet checks on the merged state and re-record
(`record_validation_evidence`). This ordering is why the owner's merge is trivial: conflicts and
integration breakage surface HERE, in your tree, not on main. Conflicts you can't resolve → report
them; the owner can fire Resolve-with-Agent.

## 2 — Tidy the commits

Squash/reorder the worktree's checkpoint commits into a few bisectable, reviewable commits
(logical groups — e.g. infra → models → views; code and its tests together) via `git rebase` /
`git reset` + fresh commits in the worktree (Bash). History on the item branch is yours to
rewrite until merge; after that it's trace.

Your shell runs at the REPO root (this thread narrates the whole item), but your git-tidy and any
re-checks belong in the WORKTREE — so scope each command into it: `git -C <worktree> …` or
`cd <worktree> && …`. A command scoped into the worktree runs autonomously (no per-command
approval); a bare mutating command at the repo root will ask the owner (it could touch main). The
worktree path is in your orient block.

## 3 — Stage the knowledge delta

If this item changed anything the anchor docs (project-prd/architecture/capabilities/decisions/
roadmap/resources) describe, stage the update NOW while context is hot: `stage_knowledge_delta(item_id, ops)` with
structured edit ops (`{doc, section, op: update|append|supersede, content}`). It validates
immediately (target section must exist, file references must be real, no placeholders) and is
applied to the docs atomically WITH the owner's merge — never edit anchor docs directly. Nothing
doc-worthy changed → stage nothing and say `none-needed` in the readiness Knowledge row.

## 4 — Draft the readiness report

A **mechanical baseline readiness.md already exists** — the loop authors it at vet→review from the
ledgers + git (derived facts: status, stats, evidence, knowledge row, freshness, a computed
recommendation). Your job is to ENRICH it, not start blank: read it, keep the derived facts, and
add the judgment a machine can't — what the change means, real risks/caveats, and whether your
recommendation differs from the mechanical one and why. Fill it as the owner's ONE decision brief:

- **Status/Vet** — what was delivered, ledger verdicts (fresh? any fails?), synced-to-main
  state.
- **Stats** — the fenced yaml, filled from ONE `git diff --stat <base>...HEAD` run in the
  worktree: file count, ±lines, test count, and the per-file rows. Counts only — the owner's
  change bars render from these numbers, so echo the command's output, never estimate.
- **Knowledge** — the delta row: what was staged and why (`updated`), `none-needed`, or a
  `stale-warning` if you noticed doc content the codebase has outgrown.
- **Warnings** — plain English, worst first: risks, behavior changes, anything a merge could
  regret. An empty warnings section must be TRUE, not optimistic.
- **Recommendation** — exactly one of `Merge` / `Hold & fix <what>` / `Merge anyway (accepting
  <risk>)`, with the reason in one line.

Anchor it in continuity (what the owner approved last, what changed since), not a cold summary.

Then render the gate report: fill `templates/gate-report.html` (in this skill's folder) — replace
its single `{{DATA_JSON}}` slot per the schema documented at its top and write the result to
`artifacts/gate-report-review.html`. The `promised` pane comes from plan.md's `## Behavior
preview` After block; the `delivered` pane is VERBATIM captured output from the evidence ledger
(never retyped); stats echo the readiness yaml. The gate check fails on any leftover `{{…}}` slot.

## 5 — Hand over the gate

Tell the owner readiness is drafted and the recommendation. Their gate: **Approve & merge** (they
fire the merge; a backup ref precedes it and revert stays offered) / **Hold & fix** (they say what
to change, in this chat — you route it, next section) / **Merge anyway** (logged override). You
never run the merge and never advance the phase.

This chat is a shared terminal — you, the owner, and the **deputy** all speak here. Feedback can
arrive from either the owner or the deputy on the owner's behalf; treat both identically (you can't
tell them apart, and you shouldn't try). Whoever gives it, hold-&-fix feedback is routed the same
way — never acted on directly in this session.

## 6 — Route hold-&-fix feedback into the loop

Hold-&-fix feedback at this gate is NEW information — a change the WORK needs, not something you do
here. This review session is un-vetted: anything you "fix" in it lands on an unverified tree and
strands the item on stale evidence. So route it — call `route_review_feedback(item_id, feedback)`
with the reviewer's words VERBATIM (their words are the record; never paraphrase). Once your turn
ends the item flips review→plan and **re-plans against the feedback**, then runs forward
(plan→build→vet→review) and is **re-vetted before it comes back** — that is what makes the feedback
converge instead of being asserted done. The plan phase scales the effort itself (a one-line tweak
or a real re-scope).

After routing: tell the owner exactly what was routed, then STOP — do not start fixing in this
session (the loop does the work and proves it; you narrate). Feedback that is genuinely NEW SCOPE
(a feature, not a shortfall of this item's requirements) goes to the inbox (`create_inbox_item`),
not the loop. A doc-only sync you already staged in §3 needs no routing — it applies at the merge.

## Pitfalls

- **A readiness report over a stale ledger** — re-vet after every sync; the report's
  vet section must describe the tree being merged.
- **Diff-dumping into the report** — the owner decides on the brief; raw diffs/logs live behind
  pointers.
