---
description: How an operational learning is captured and made into a governed artifact: the candidate→proposal→published lifecycle, its two gates and statuses, and where each lives. Use when you touch capture/distill/forge, a gate, or need what "learning" means in SuperMe. Not the project-knowledge model — that's dev-knowledge-structure.
enabled: true
---

# Learning pipeline

SuperMe's operational self-improvement path: a durable learning noticed in a session becomes a governed
artifact, owner-gated at two points. It runs for every repo and every scope — *learning is not dev-only*.

## The path
deterministic sweep → **capture** (files candidates) → **distill** (gates + consolidates into proposals)
→ **gate 1** (owner ratifies / edits / re-classifies) → **forge** (authors + validates + stages) →
**gate 2** (owner reviews the staged artifact + eval, then publishes). Every run is traced in the activity log.

## The two items
- **Candidate** — one raw, unfiltered learning mined from a conversation slice (`signal` · `rationale` ·
  `evidence` · `scope_hint` · `form_hint`). Noisy by design; distill is the filter.
- **Proposal** — distill's typed, consolidated, owner-ratifiable unit: `output_form`, `target_scope`,
  `title`/`summary`/`body`, the typed `fields` spec, `confidence`, `cluster`, its source `candidate_ids`,
  and — once forged — the `staged_artifact` + `eval_report`.

## Enums
- **output_form** — what it becomes: `constitution` · `skill` · `agent` (knows / does / delegates).
- **target_scope** — where it lands: `repo_dev` (this project) · `universal_dev` (any project) ·
  `core` (SuperMe's character).

## Statuses
- **Candidate:** `candidate` (open) → `processed` (folded into a proposal). Noise is **dropped** —
  removed, not logged.
- **Proposal:** `proposed` (distilled) →[gate 1]→ `writing` (forge running) → `drafted` (staged)
  →[gate 2]→ `published` (written to disk). Terminal: `rejected` · `superseded` · `retired` (a published
  artifact the owner later deleted).

## Where it lives
- Candidates + proposals: **`.dev.db`** (`memory_candidate` / `memory_proposal`), per context.
- A staged artifact lives **in the proposal row** (`staged_artifact`/`staged_path`/`eval_report`) —
  **nothing reaches disk until gate-2 publish**.
- On publish it lands in its real home: a **constitution** under the scope's constitution dir, a **skill**
  at `skills/<slug>/SKILL.md`, an **agent** at `agents/<slug>.md`.
- Every capture/distill/forge run: **`.system.db`** (`run` + `run_event`) → the activity log.
