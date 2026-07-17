---
name: deliver
description: "Make a validated work-item mergeable and draft its readiness report: freshness-sync from main, tidy commits, write readiness.md for the owner's merge decision. Use when a work-item is in its deliver phase; not for running the validation checks themselves (use validate) — and the merge itself is the owner's action, never yours."
argument-hint: "work-item id (optional — defaults to the bound item)"
category: workspace
---

# Deliver a work-item

Get the item's branch into a state the owner can merge on a report alone — they decide on
`readiness.md`, never on a diff. You prepare; the merge button is theirs.

## 1 — Freshness first

Run `sync_from_main` (commit any loose work first). If it merged trunk changes in, the evidence
ledger just went stale — re-run the validation checks on the merged state and re-record
(`record_validation_evidence`). This ordering is why the owner's merge is trivial: conflicts and
integration breakage surface HERE, in your tree, not on main. Conflicts you can't resolve → report
them; the owner can fire Resolve-with-Agent.

## 2 — Tidy the commits

Squash/reorder the worktree's checkpoint commits into a few bisectable, reviewable commits
(logical groups — e.g. infra → models → views; code and its tests together) via `git rebase` /
`git reset` + fresh commits in the worktree (Bash). History on the item branch is yours to
rewrite until merge; after that it's trace.

## 3 — Stage the knowledge delta

If this item changed anything the anchor docs (architecture/spec/roadmap/project-prd/resources)
describe, stage the update NOW while context is hot: `stage_knowledge_delta(item_id, ops)` with
structured edit ops (`{doc, section, op: update|append|supersede, content}`). It validates
immediately (target section must exist, file references must be real, no placeholders) and is
applied to the docs atomically WITH the owner's merge — never edit anchor docs directly. Nothing
doc-worthy changed → stage nothing and say `none-needed` in the readiness Knowledge row.

## 4 — Draft the readiness report

`scaffold_artifact(item_id, "readiness")`, then fill it as the owner's ONE decision brief:

- **Status/Validation** — what was delivered, ledger verdicts (fresh? any fails?), synced-to-main
  state.
- **Knowledge** — the delta row: what was staged and why (`updated`), `none-needed`, or a
  `stale-warning` if you noticed doc content the codebase has outgrown.
- **Warnings** — plain English, worst first: risks, behavior changes, anything a merge could
  regret. An empty warnings section must be TRUE, not optimistic.
- **Recommendation** — exactly one of `Merge` / `Hold & fix <what>` / `Merge anyway (accepting
  <risk>)`, with the reason in one line.

Anchor it in continuity (what the owner approved last, what changed since), not a cold summary.

## 5 — Hand over the gate

Tell the owner readiness is drafted and the recommendation. Their gate: **Merge** (they fire the
merge; a backup ref precedes it and revert stays offered) / **Hold & fix** (their feedback
re-enters as scoped input — fix, re-validate, re-present; a bounded loop, not a new plan) /
**Merge anyway** (logged override). You never run the merge and never advance the phase.

## Pitfalls

- **Merging yourself** — the item→main merge is the owner's single heavy gate; there is no agent
  path to it by design.
- **A readiness report over a stale ledger** — re-validate after every sync; the report's
  validation section must describe the tree being merged.
- **Diff-dumping into the report** — the owner decides on the brief; raw diffs/logs live behind
  pointers.
