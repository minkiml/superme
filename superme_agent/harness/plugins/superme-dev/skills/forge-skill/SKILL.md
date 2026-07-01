---
name: forge-skill
description: Author a complete SKILL.md from an approved skill proposal, then prove it with the forge_kit lint + behavioural eval. Use when forging an approved skill proposal into its final artifact.
category: learning
access: silent
---

# Forge a skill

You are turning an approved proposal into the **complete SKILL.md** for a new skill, and proving it
holds up before you stage it. The proposal prompt gives you the spec — `fields`
(`name`, `when_to_use`, `procedure`, `tools`, `scope`), the `summary` (intent), the `target_scope`,
the publish **slug**, and the **forge_kit path**. Author from the spec; don't invent behaviour it
doesn't support.

A skill is a recipe the **main agent runs in its own context**, step by step. So write for that
reader: lean, concrete, in the imperative.

## What makes a SKILL.md good

- **The `description` is the whole routing decision.** It is the only thing an agent sees when
  choosing the skill. Form it as `<what it does>. Use when <specific triggers>`, third person, with
  the contexts/keywords that should fire it — and, where useful, a near-miss that should *not*.
  Vague descriptions make a skill invisible. (≤1024 chars, no angle brackets.)
- **`name` is kebab-case and equals the slug** in the prompt — it becomes the on-disk folder.
- **Lean body, progressive disclosure.** Keep SKILL.md tight (aim well under 200 lines). A numbered
  procedure the agent can follow without guessing. Push bulky detail into `references/<file>.md` and
  point at it just-in-time — only if a step earns it.
- **Explain the why, don't just command.** A short reason for a non-obvious step beats an ALL-CAPS
  MUST; the model follows reasoning better than edicts.
- **Bundle a script for deterministic, repeated work.** If a step is fragile parsing or the same
  boilerplate every run, put a tiny CLI in `scripts/` and call it, rather than re-deriving it each
  time. (Note it in your stage `note` so the reviewer knows it's there.)
- **Instructions for doing the task only** — no narration of the pipeline or other agents.

## Template to copy

```markdown
---
name: <slug>
description: <what it does>. Use when <specific triggers, keywords, contexts>.
category: <optional>
---

# <Title>

<One line on what this skill is for.>

## Steps

1. <First action — imperative, concrete.>
2. <Next action. If a branch: "If X, do Y; otherwise skip to step N.">
3. <…>

## Notes

<Only if they pull weight: a gotcha, an error path, or "See `references/x.md` when …".>
```

## Procedure

1. **Author** the SKILL.md from `fields` into a working file in the scratch workspace named in the
   prompt (e.g. `<workspace>/_draft_skill.md`). Sharpen the `procedure` outline into clear numbered
   steps; write the trigger-rich `description`.
2. **Lint** — structural check:
   `python <forge_kit>/lint.py skill <workspace>/_draft_skill.md --name <slug>`
   Fix every `ERROR`, weigh each `WARN`, re-run until it prints `PASS`.
3. **Eval** — behavioural check:
   `python <forge_kit>/eval.py skill <workspace>/_draft_skill.md --intent "<summary>"`
   It returns a verdict + per-lens scores + concrete `issues` (each with a fix).
4. **Improve** — if the verdict is `fail` OR there are any `high`-severity issues, apply the fixes,
   then re-run lint + eval (step 2→3). Do this **at most once more** (two eval rounds total — enough
   to fix real defects without burning the run). `warn` with only `low` issues is good to ship.
5. **Stage** — call `mcp__dev__stage_artifact` once with the final SKILL.md as `content` and the
   **last** eval report JSON (its final line) as `eval_report`, plus an optional one-line `note`.

Do not stage before lint passes. The eval is evidence for the gate-2 reviewer, not a wall — if it
stays `fail` after the improve pass, stage anyway with a `note` saying what's unresolved and why.
