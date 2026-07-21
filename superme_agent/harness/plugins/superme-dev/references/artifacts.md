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

Every artifact's frontmatter carries `reader: user | agent | both` (stamped by the scaffolder) —
a LABEL saying who the doc is designed for, never a constraint. `user` docs render prominently in
the drilldown; `agent` plumbing collapses.

| artifact | file | emitted by | consumed by | notes |
|---|---|---|---|---|
| plan | `artifacts/plan.md` | plan phase | pre-main gate | per-kind template; section contract below |
| validation | `artifacts/validation.md` | vet (ledger; checks come from plan.md `## Vet plan`) | review gate | evidence ledger; stale-on-edit |
| vet-report | `artifacts/vet-report-<n>.md` | vet phase, one per cycle (`file_vet_report`) | next build cycle + review gate | envelope code-owned; verdicts must match the ledger; findings describe, never prescribe |
| readiness | `artifacts/readiness.md` | review phase (per loop pass) | review gate | the A/B/C brief; `## Stats` fenced yaml (files/insertions/deletions/tests + by_file rows, echoed from `git diff --stat`) feeds the gate's change bars |
| findings | `artifacts/findings.md` | investigate/report | close gate | research deliverable |
| closeout | `artifacts/closeout.md` | close phase | close gate | tri-split; Facts yaml claims ground-truth-verified |
| checkpoint | `checkpoints/<ts>.md` | session end + pre-compaction | next session's cold start | append-only, atomic |
| notes | `notes/` | any phase | nobody (agent scratch) | free-form, never a gate |
| handoff-brief | inbox `<id>/handoff-brief.md` → item `preliminary/` | itemize time | triage + plan | high-level ONLY: no plans, no implementation detail, no research findings |
| gate-report | `artifacts/gate-report-<phase>.html` | the phase skill, from its `templates/gate-report.html` | the owner (embedded in the drilldown gate card) | `reader: user`; template owns ALL style/layout — the agent only fills `{{SLOT}}` placeholders; a leftover slot fails the gate check |

Task lines live INSIDE plan.md under `## Tasks` (`- [ ]` / `- [x]`) — there is no separate tasks
doc; build sessions tick the boxes there.

## plan.md — the section contract (implementation kind)

The authoritative field spec for every plan.md section (the skill teaches the judgment; the
fields live here). The scaffold writes them in this order; the pre-main gate validates them
mechanically. Three of them are GATE FEEDS — structured content the owner's gate report renders
as visuals, so their shape matters as much as their truth.

- **`## Approach`** — prose: what we'll build and how.
- **`## Touches`** *(gate feed → the change map)* — ONE fenced yaml list; one row per component
  the plan touches:
  ```yaml
  - component: <short name>    # the label a node gets on the map
    path: <repo-relative path> # file or dir
    action: new | modify | read
  ```
  `read` = consulted but unchanged (context the builder needs). Rows must parse — broken yaml
  blocks the gate.
- **`## Behavior preview`** *(gate feed → the before/after panes)* — exactly two fenced blocks:
  `**Before**` (the observable surface today — command output, screen, API shape) then
  `**After**` (the PREDICTED surface once the plan lands, same surface + format so the panes
  compare line-for-line). Keep each pane ≤ ~15 lines; verbatim-shaped, never a description of
  the output.
- **`## Tasks`** — ordered `- [ ]` items, each small enough to verify on its own; THE task
  tracker (progress = checkbox ratio).
- **`## Risks & assumptions`** *(gate feed → the confirm/adjust cards)* — one bullet per
  assumption made without the owner and per risk worth their eyes; each ONE line, concrete
  enough to confirm or veto. `- none` only when truly none.
- **`## Inner checks`** — bullet list of commands whose EXIT CODE decides them; build must run
  these green before it may exit.
- **`## Vet plan`** — the contract a fresh vet agent executes. Header lines: `depth:`
  (`none | checks | scenarios`) · `reason:` (one line, required even for none) · `env:`
  (recipe id or none). Then one `### <check-id>` per check (lowercase slug — it keys the
  evidence ledger) with four `- key: value` fields:
  - `traces:` — the written requirement this check defends (PRD deliverable / user story /
    spec decision). No traces → not a requirement → it can't gate the loop.
  - `mode:` — `command` (exit code / literal output match) · `interaction` (agent drives the
    real thing and judges vs expect — requires a real `env`) · `inspection` (agent reads code
    against a stated bar). If the scenario is runnable as plain shell commands in the worktree
    (a CLI, a script), that is `command` — even when the judgment is about output format;
    `interaction` is only for surfaces that need a live app/environment to drive.
  - `scenario:` — the real steps, concretely: commands verbatim; UI steps as a user takes them.
  - `expect:` — a falsifiable pass condition (exact output / state / rendered text).

  Hard rules the gate enforces: legal depth · reason present · depth `none` ⇔ zero checks ·
  every check fully fielded, legal mode, unique slug id · `interaction` requires an env recipe.
