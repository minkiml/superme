# Work-item artifacts — the single-source authoring contract

Every work-item artifact follows one playbook: **code supplies form, the agent supplies content.**
The executable single source is `superme_agent/core/artifacts.py` (template loading · scaffolder ·
self-checks · verification recording · checkpoints). Section skeletons live
as ONE template file each under their authoring skill's `templates/` folder — skills never restate
section lists; the self-check is derived from the template's own headings.

Terminology: **validation** = build's own internal checks (unit tests, compile, mocks, synthetic
errors). **verification** = vet's real, practical, safely-isolated tests (scenario runs, live
checks). Vet verifies; it does not validate.

## The playbook (applies to every kind)

1. **Scaffold first** — call `scaffold_artifact(item_id, artifact)`. Code writes the skeleton
   (frontmatter, section order, timestamps); re-scaffolding an existing file is a no-op, never an
   overwrite.
2. **Fill the `<fill:…>` slots only.** Keep the headings; replace each slot with real content.
   A comment-only section (a pen's or the driver's) is not yours to write.
3. **The gate self-checks.** The phase gate that CONSUMES the doc runs the validator (template
   sections present, fill-bearing sections filled, no slots left). A failed check is an itemized,
   retry-shaped list — fix and re-present; nothing was persisted or advanced.
4. **Facts are verified against ground truth.** Closeout claims (changed files, merge commit,
   artifact paths) are checked for real existence before the close gate accepts.
5. **Verification is earned, never asserted.** `record_verification` appends machine entries
   (check · how · result · pass/fail) into the current cycle report's §Verification fence,
   fingerprinted against the repo state; ANY later repo edit flips the derived verdict to
   `stale` — re-run checks after changes.
6. **Checkpoints are append-only continuity.** `write_checkpoint` banks conversation-native
   reasoning (working-on / decisions / remaining / notes). Reference artifacts BY PATH — never
   duplicate their content.

## The kinds

Every artifact's frontmatter carries `reader: user | agent | both` (stamped by the scaffolder) —
a LABEL saying who the doc is designed for, never a constraint.

**Agent-facing docs (`artifacts/` — the source of truth):**

| artifact | file | writer · when | reader |
|---|---|---|---|
| brief | `artifacts/brief.md` | triage (the problem + its classification) | plan |
| plan | `artifacts/plan.md` | plan; updated onwards (overwrite + `## Decisions & clarifications` append) | build · vet · review |
| cycle report | `artifacts/build-vet-<n>.md` | staged: build (§Built/§Validation) → vet's pen (§Verification fence) → loop driver (§Cycle outcome) | vet (handover) · next build cycle · review |
| authorizations | `artifacts/authorizations.md` | `request_authorization` (build) · grants/denies at review | vet (skip deferred) · review gate |
| investigation | `artifacts/investigation.md` | investigate (research's work-segment record — the counterpart of the cycle report) | review |
| checkpoint | `checkpoints/<ts>.md` | any long session · pre-compaction | next session's cold start |
| handoff-brief | inbox `<id>/handoff-brief.md` → item `preliminary/` | itemize time | triage |

**User-facing reports (`reports/` — projections; every line traces to a named agent doc, no new
facts; compact — tables over paragraphs; overwrite in place on re-runs + fill `## Changed since`):**

| report | template | writer · when |
|---|---|---|
| `reports/report-triage.md` | `skills/triage/templates/report-triage-template.md` | triage, closing act |
| `reports/report-plan.md` | `skills/plan/templates/report-plan-template.md` | plan, closing act |
| `reports/report-build.md` | `skills/build/templates/report-build-template.md` | build, at each cycle's end (overwrite — the final cycle's version is the loop-exit report) |
| `reports/report-vet.md` | `skills/vet/templates/report-vet-template.md` | vet's `file_vet_report` pen (table derived from the recorded checks) |
| `reports/report-investigate.md` | `skills/investigate/templates/report-investigate-template.md` | investigate, at each session's end (overwrite — reports the state of the search, not its conclusions) |
| `reports/report-review.md` | per kind, both in `skills/review/templates/`: implementation → `report-review-template.md` · research → `report-review-research-template.md` | the `review` skill's ENTRY run, fired on entry to `review` for every kind |
| `reports/report-close.md` | `skills/close/templates/report-close-template.md` | close, its closing act |

`report-review.md` is ONE slot with a per-kind template, exactly as `plan.md` is — the slot means
"the document the owner decides on at review"; the kind supplies its shape. Implementation's
(written by `synthesize`) lands with its own slice.

**Retired (2026-07-27, workflow-renovation-v2 §3.1 demolition):** `assumptions.md` + the
`record_assumption` tool — an agent's judgment call now goes in its own phase record's
`## Assumptions` section and surfaces in that phase's report · `readiness.md` — said what
`report-review.md` already says · `closeout.md` — close's output is `reports/report-close.md` +
the DB close record · `validation.md` — folded into `plan.md ## Verification plan`.
Also retired: `findings.md` —
the research record is now `artifacts/investigation.md` (evidence, agent-facing) and its verdicts
live in `reports/report-review.md`, where the decision is actually made.

Task lines live INSIDE plan.md under `## Tasks` (`- [ ]` / `- [x]`) — there is no separate tasks
doc; build sessions tick the boxes there.

## plan.md — the section contract (implementation kind)

Sections and order come from `skills/plan/templates/plan-template.md`; the pre-main gate validates
them mechanically. What each must achieve:

- **`## Intent`** — 1–3 lines, the outcome this plan aims at, answering brief.md's `## Problem`.
- **`## Design`** — the approach and why this way · modules/files touched · interfaces and data
  shapes (signatures, schemas, routes) · constraints and gotchas from directed reads · explicitly
  out of scope. Build implements this section and MUST NOT amend it — a design that no longer
  fits reality goes back through plan, never gets silently rewritten mid-build. A `## Design`
  outgrowing a section is the SPLIT signal: propose splitting the item rather than writing a
  design document.
- **`## Decisions & clarifications`** — owner Q&A conclusions, append-only. Starts empty.
- **`## Tasks`** — ordered `- [ ]` items, each small enough to verify on its own and naming the
  Design part it implements; THE task tracker (progress = checkbox ratio).
- **`## Verification plan`** — the contract a FRESH vet agent with zero context executes; build
  MUST NOT amend it (it is the exam). Header lines: `depth:` (`none | checks | scenarios`) ·
  `reason:` (one line, required even for none) · `env:` (recipe id or none). Then one
  `### <check-id>` per check (lowercase slug — it keys the recorded entries) with five
  `- key: value` fields:
  - `traces:` — the written requirement this check defends (PRD deliverable / user story /
    design decision). No traces → not a requirement → it can't gate the loop.
  - `covers:` — the `## Tasks` id(s) this check proves (`t1, t3`), or blank for a genuinely
    whole-item check (a suite run, a lint pass). This is the Proof view's JOIN KEY: it is what
    turns a grid of check ids into "this feature, proven this way". Blank is a legitimate answer;
    a task id no `## Tasks` line declares is not.
  - `mode:` — `command` (exit code / literal output match) · `interaction` (agent drives the
    real thing and judges vs expect — requires a real `env`) · `inspection` (agent reads code
    against a stated bar). If the scenario is runnable as plain shell commands in the worktree,
    that is `command` — even when the judgment is about output format; `interaction` is only for
    surfaces that need a live app/environment to drive.
  - `scenario:` — the real steps, concretely: commands verbatim; UI steps as a user takes them.
  - `run:` — OPTIONAL. One shell command whose EXIT CODE is the verdict (`&&` chains steps). A
    check that carries one is executed by the KERNEL, in the sandbox, before the vet session
    opens, and its result is recorded as machine evidence — free to re-run every cycle, and not
    revisable by anything downstream. Omit it when the pass condition needs a judge (a UI that
    must look right, prose that must read well); never bend judgment into a command to earn the
    label.
  - `expect:` — a falsifiable pass condition (exact output / state / rendered text).

  Hard rules the gate enforces: legal depth · reason present · depth `none` ⇔ zero checks ·
  every check fully fielded, legal mode, unique slug id · `interaction` requires an env recipe.

**Three zones, and the order is the guarantee.** The prose above is frozen at the first plan;
revisions APPEND (`## Revision log` + one `## Revision r<n>` block each, both written by
`revise_plan` — never by hand); and `## Tasks` + `## Verification plan` stay LAST, mutated in place.
Read top to bottom: what we intended → how it changed → what is true now. Because the live pair is
structurally last, nothing below them can contradict them — they are current for the item's whole
life, however many revisions it took.

## plan.md — the section contract (research kind)

Sections and order come from `skills/plan/templates/plan-research-template.md`. What each must
achieve:

- **`## Questions`** — what the investigation must answer, one per line. Each has to be answerable
  from sources that exist and worth the time it will cost; a question nobody will act on is scope.
- **`## Method`** — per question or cluster: which sources, which code, which experiments.
- **`## Boundaries`** — what will NOT be investigated. These are the walls investigate treats as
  hard: a thread leading outside them becomes a follow-up, not a detour.
- **`## Done criteria`** — falsifiable per question: the evidence that ends the search. This is the
  research kind's exam — there is no `## Verification plan` because a research item builds nothing
  and never enters the build⟷vet loop.
- **`## Decisions & clarifications`** / **`## Tasks`** — as the implementation kind, `## Tasks`
  last (the live zone).

## build-vet-<n>.md — the cycle report

Scaffolded by the kernel at each build cycle's start from
`skills/build/templates/build-vet-template.md`. Strictly sequential writers:

- **`## Built`** (build) — what was implemented per task · files touched · how to exercise it ·
  errors/gaps/concerns · assumptions made · authorization request ids. **Per-task bullets LEAD with
  the task id** (`- t2 — …`); a bullet belonging to no single task leads with none and reads as
  item-wide.
- **`## Validation`** (build) — the internal checks run, results verbatim; same task-id lead.
- **`## Verification`** (the `record_verification` / `record_diagnosis` pens ONLY — vet has no file
  writes) — the fenced ledger the loop driver parses. Two kinds of entry share it:
  - a **verdict** — did the check pass. Carries `by:` — `machine` when the kernel ran the check's
    `run:` block itself, `agent` when a vetter performed it and attested. A machine entry is that
    cycle's last word on its check; a later agent entry against it is refused.
  - a **diagnosis** (`kind: diagnosis`) — `where:` it broke, `why:`, and optionally `unknown:`.
    Required on every failing check each cycle (the vet report refuses without it) and never the
    fix. It is a separate entry because a kernel-run failure has two authors: the daemon owns the
    exit code, vet owns the reading of it.
- **`## Cycle outcome`** (loop driver) — decision + reason; closes the cycle.

This file IS the build→vet handover (vet reads §Built/§Validation instead of re-deriving from a
raw diff) and the failed cycle's report is the next build cycle's work order.
