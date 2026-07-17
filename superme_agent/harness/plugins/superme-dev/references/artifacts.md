# Work-item artifacts — the single-source authoring contract

Every work-item artifact follows one playbook: **code supplies form, the agent supplies content.**
The executable single source is `superme_agent/core/artifacts.py` (templates · scaffolder ·
self-checks · evidence ledger · checkpoints · closeout verification). Skills NEVER restate
skeletons or section lists — they cite this contract and call the tools.

## The playbook (applies to every kind)

1. **Scaffold first** — call `scaffold_artifact(item_id, artifact)`. Code writes the skeleton
   (frontmatter, section order, timestamps); re-scaffolding an existing file is a no-op, never an
   overwrite.
2. **Fill the `<fill:…>` slots only.** Keep the headings; replace each slot with real content.
   Remove a slot only by filling its section.
3. **The gate self-checks.** The phase gate that CONSUMES the doc runs the validator (required
   sections present + filled, no slots left). A failed check is an itemized, retry-shaped list —
   fix and re-present; nothing was persisted or advanced.
4. **Facts are verified against ground truth.** Closeout claims (changed files, merge commit,
   artifact paths) are checked for real existence before the close gate accepts.
5. **Evidence is earned, never asserted.** `record_validation_evidence` appends machine entries
   (check · how · result · pass/fail) fingerprinted against the repo state; ANY later repo edit
   flips the ledger to `stale` — re-run checks after changes.
6. **Checkpoints are append-only continuity.** `write_checkpoint` banks conversation-native
   reasoning (working-on / decisions / remaining / notes). Reference artifacts BY PATH — never
   duplicate their content.

## The kinds (D6 taxonomy)

| artifact | file | emitted by | consumed by | notes |
|---|---|---|---|---|
| plan | `artifacts/plan.md` | plan phase | pre-main gate | per-kind template; `## Tasks` checkboxes ARE the task tracker (progress derived from the ratio) |
| validation | `artifacts/validation.md` | validate (ledger; criteria come from plan.md) | deliver gate | evidence ledger; stale-on-edit |
| readiness | `artifacts/readiness.md` | deliver phase (per loop pass) | deliver gate | the A/B/C brief |
| findings | `artifacts/findings.md` | investigate/report | close gate | research deliverable |
| closeout | `artifacts/closeout.md` | close phase | close gate | tri-split; Facts yaml claims ground-truth-verified |
| checkpoint | `checkpoints/<ts>.md` | session end + pre-compaction | next session's cold start | append-only, atomic |
| notes | `notes/` | any phase | nobody (agent scratch) | free-form, never a gate |
| handoff-brief | inbox `<id>/handoff-brief.md` → item `preliminary/` | itemize time | triage + plan | high-level ONLY: no plans, no implementation detail, no research findings |

Task lines live INSIDE plan.md under `## Tasks` (`- [ ]` / `- [x]`) — there is no separate tasks
doc; build sessions tick the boxes there.
