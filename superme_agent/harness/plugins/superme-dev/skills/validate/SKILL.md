---
name: validate
description: "Validate a work-item's built work against its plan's validation criteria, recording machine evidence for every check. Use when a work-item is in its validate phase or built work needs verification before delivery; not for writing new features (use build) or for drafting the merge readiness report (use deliver)."
argument-hint: "work-item id (optional — defaults to the bound item)"
category: workspace
---

# Validate a work-item

Prove the built work does what the plan promised — with machine evidence, never claims. The
evidence ledger is what the deliver gate's readiness report stands on.

## 1 — The checklist comes from the plan

`plan.md`'s **Validation criteria** section is the authoritative list. Scaffold the artifact if
missing (`scaffold_artifact(item_id, "validation")`) and copy the criteria into its Checklist —
add checks reality demands (regressions, lint, type-check), never drop ones the plan named.

## 2 — Run every check; record every result

Run each check in the item's worktree. After EACH one — pass or fail — call
`record_validation_evidence` with the exact command, the machine result (exit code, counts, output
tail), and the verdict. Failures are recorded too: the ledger is a lab notebook, not a highlight
reel, and an unrecorded check doesn't exist as far as the gate is concerned.

## 3 — Failures loop through fixes, then STALE means re-run

Fix small failures right in the worktree, commit, and re-run the affected checks. Every edit after
a green entry flips the ledger STALE by design (the fingerprint moved) — the readiness report
shows staleness, so finish with a full green pass over the final tree, not a patchwork of greens
from different states. A failure that needs real re-design goes back to the owner: say what broke
and recommend returning to build.

## 4 — Close the phase

When the ledger is green and fresh: tick the validation Checklist boxes, bank a checkpoint if the
session ran long, and tell the owner validation is complete — one line per criterion, verdicts
only. The owner advances to deliver.

## Pitfalls

- **Claiming without recording** — "tests pass" with no ledger entry is invisible to every
  downstream gate; the tool call IS the validation.
- **Leaving the ledger stale** — evidence from before your last edit proves nothing about the tree
  the owner will merge.
- **Silently skipping a plan criterion** — if a criterion is obsolete, say so and strike it with
  the owner, don't just not run it.
