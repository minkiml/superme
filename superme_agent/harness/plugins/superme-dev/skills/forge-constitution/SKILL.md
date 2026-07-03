---
name: forge-constitution
description: Forge an approved constitution proposal into its final artifact (frontmatter-first) and prove it with the forge_kit lint + eval. Use when an approved constitution proposal needs forging.
category: learning
access: silent
---

# Forge a constitution item

You are turning an approved proposal into a **constitution** — a unit of SuperMe's own operational
intelligence: a convention, a small reference, or a contract/schema detail that shapes how the agent
works for its scope. (Not a procedure — that's a skill. Not transient state — that's a work-item.)
The proposal gives you the spec: `fields` (`statement`, `scope`, `rationale`), the `summary`
(intent), the `target_scope`, and the **forge_kit path**.

## Keep it focused
Forge one **focused increment** — a single convention, a small reference, or one contract detail —
self-contained and sharp. Don't reach for a comprehensive expertise pack: a full `sql-expert` is
built deliberately over time, not in one pass. If the proposal over-reaches, forge the small, true
piece of it and let it accrete across passes.

## Frontmatter-first — the shape that matters
The agent always sees a constitution's **`description`** (it sits in the always-on catalog) and pulls
the body only when it needs more. Two flavors:
- **rule / convention** — the `description` *is* the directive, obeyable from that line alone
  ("Always run the formatter before committing"). Body = optional why / example, or omitted.
- **reference / contract** — the `description` says *what it covers and when to pull it*; the **body
  carries the substance** (the reference or schema itself).

In both: one coherent item (no "and"-joined rules), imperative and concrete, no conflict with rules
already in force. Author only `description` (+ optional body); publish stamps name/enabled/scope/etc.

## Template to copy
```
---
description: <the catalog line — for a rule, the directive itself; for a reference, what it covers + when to pull>
---
<body: a rule's optional why/example, OR a reference's actual content. Omit for a self-evident rule.>
```

## Procedure
1. **Author** into a scratch file (e.g. `<workspace>/_draft_constitution.md`): the `description`
   frontmatter + body, per the flavor above.
2. **Lint** — `python <forge_kit>/lint.py constitution <file>` — fix every `ERROR` (missing/multi-rule
   `description`; a rule-body that only restates its description), weigh `WARN`s, re-run to `PASS`.
3. **Eval** — `python <forge_kit>/eval.py constitution <file> --intent "<summary>"` (add
   `--existing <path>` if the prompt names the scope's existing rules).
4. **Improve** — if `fail` or any `high` issue, tighten and re-run (at most once more).
5. **Stage** — `mcp__dev__stage_artifact` once, the full frontmatter'd artifact as `content` + the
   last eval report JSON as `eval_report` (+ optional `note`).

Do not stage before lint passes. If eval stays `fail` after the improve pass, stage anyway with a
`note` on what's unresolved.
