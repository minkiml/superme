---
name: forge-constitution
description: Forge an approved constitution proposal into its final artifact (frontmatter-first) and prove it with the forge_kit lint + eval. Use when an approved constitution proposal needs forging.
category: learning
access: silent
---

# Forge a constitution item

You are turning an approved proposal into a **constitution** — a unit of SuperMe's operational
intelligence — and proving it holds up before you stage it. The proposal gives you the spec: `fields`
(`statement`, `scope`, `rationale`), the `summary` (intent), the `target_scope`, and the **forge_kit
path**.

**Author to the standard in [`references/writing-constitutions.md`](references/writing-constitutions.md)**
— read it first: it carries what a constitution is, the two axes to classify on, the rules, the
frontmatter, the template, and the checklist. Forge **one focused increment**; if the proposal
over-reaches, forge the small true piece of it and let the rest accrete across later passes.

## Procedure
Run the shell steps below with `Bash`.
1. **Author** into a scratch file (e.g. `<workspace>/_draft_constitution.md`), following the reference.
2. **Lint** — `python <forge_kit>/lint.py constitution <file>` — fix every `ERROR`, weigh `WARN`s, re-run
   to `PASS`.
3. **Eval** — `python <forge_kit>/eval.py constitution <file> --intent "<summary>"` (add
   `--existing <path>` if the prompt names the scope's existing rules).
4. **Improve** — if `fail` or any `high` issue, tighten and re-run (at most once more).
5. **Stage** — `mcp__dev__stage_artifact` once: the full frontmatter'd artifact as `content` + the last
   eval report JSON as `eval_report` (+ optional `note`).

Do not stage before lint passes. If eval stays `fail` after the improve pass, stage anyway with a `note`
on what's unresolved.
