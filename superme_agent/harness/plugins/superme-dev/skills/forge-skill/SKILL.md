---
name: forge-skill
description: Author a complete SKILL.md from an approved skill proposal, then prove it with the forge_kit lint + behavioural eval. Use when forging an approved skill proposal into its final artifact.
category: learning
access: silent
---

# Forge a skill

You turn an approved proposal into the **complete SKILL.md** for a new skill, and prove it holds up
before you stage it. A skill is a recipe the **main agent runs in its own context**, step by step —
so write for that reader: lean, concrete, imperative.

The proposal prompt is your spec: `fields` (`name`, `when_to_use`, `procedure`, `tools`, `scope`), the
`summary` (intent), the `target_scope`, the publish **slug**, and the **forge_kit path**. Author from
the spec; don't invent behaviour it doesn't support.

**Author to the standard in [`references/writing-skills.md`](references/writing-skills.md) — read it
first.** It carries the rules (description-as-router, goals-not-a-railroad, progressive disclosure),
the frontmatter, the template, and the checklist you author against.

## Procedure
Run the shell steps below with `Bash`.

1. **Author** the SKILL.md from `fields` into a working file in the scratch workspace named in the
   prompt (e.g. `<workspace>/_draft_skill.md`), following `references/writing-skills.md`. Sharpen the
   `procedure` into clear numbered steps; write the trigger-rich `description`.
2. **Lint** — `python <forge_kit>/lint.py skill <workspace>/_draft_skill.md --name <slug>`.
   Fix every `ERROR`, weigh each `WARN`, re-run until it prints `PASS`.
3. **Eval** — `python <forge_kit>/eval.py skill <workspace>/_draft_skill.md --intent "<summary>"`.
   It returns a verdict + per-lens scores + concrete `issues` (each with a fix).
4. **Improve** — if the verdict is `fail` OR there are any `high`-severity issues, apply the fixes and
   re-run lint + eval (step 2→3). Do this **at most once more** (two eval rounds total). `warn` with
   only `low` issues is good to ship.
5. **Stage** — call `mcp__dev__stage_artifact` once with the final SKILL.md as `content` and the
   **last** eval report JSON (its final line) as `eval_report`, plus an optional one-line `note`.

Don't stage before lint passes. The eval is evidence for the gate-2 reviewer, not a wall — if it stays
`fail` after the improve pass, stage anyway with a `note` saying what's unresolved and why.
