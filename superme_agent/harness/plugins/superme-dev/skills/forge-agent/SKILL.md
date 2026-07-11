---
name: forge-agent
description: Author a complete agent.md from an approved agent proposal, then prove it with the forge_kit lint + behavioural eval. Use when forging an approved agent proposal into its final artifact.
category: learning
access: silent
---

# Forge an agent

You turn an approved proposal into the **complete agent.md** for a new sub-agent, and prove it holds up
before you stage it. A sub-agent is an **isolated worker**: it runs in its own context and sees only the
brief you write plus the prompt it's handed — never the conversation that spawned it. So the brief must
stand alone: its inputs, its single job, and exactly what it returns.

The proposal prompt is your spec: `fields` (`name`, `role`, `tools`, `model`, `trigger`), the `summary`
(intent), the `target_scope`, the publish **slug**, and the **forge_kit path**. Author from the spec;
don't invent scope or tools it doesn't support.

**Author to the standard in [`references/writing-agents.md`](references/writing-agents.md) — read it
first.** It carries the self-contained skeleton (role · Task · **Inputs** · Workflow · fenced Return ·
Critical Rules), the tool-scoping and point-of-use rules, the full frontmatter table (note the SuperMe
override: `model` must be an **alias**), the template, and the checklist you author against.

## Procedure
Run the shell steps below with `Bash`.

1. **Author** the agent.md from `fields` into a working file in the scratch workspace named in the
   prompt (e.g. `<workspace>/_draft_agent.md`), following `references/writing-agents.md`. Write the
   delegation-clear `description`, scope `tools`, name the worker's inputs, and fence its return
   contract. Normalise `fields.model` to an alias (or drop it to inherit).
2. **Lint** — `python <forge_kit>/lint.py agent <workspace>/_draft_agent.md --name <slug>`.
   Fix every `ERROR`, weigh each `WARN`, re-run until it prints `PASS`.
3. **Eval** — `python <forge_kit>/eval.py agent <workspace>/_draft_agent.md --intent "<summary>"`.
   It returns a verdict + per-lens scores + concrete `issues`; high-severity ones are usually a missing
   input or return contract.
4. **Improve** — if the verdict is `fail` OR there are any `high`-severity issues, apply the fixes and
   re-run lint + eval (step 2→3). Do this **at most once more** (two eval rounds total). `warn` with
   only `low` issues is good to ship.
5. **Stage** — call `mcp__dev__stage_artifact` once with the final agent.md as `content` and the
   **last** eval report JSON (its final line) as `eval_report`, plus an optional one-line `note`.

Don't stage before lint passes. The eval is evidence for the gate-2 reviewer, not a wall — if it stays
`fail` after the improve pass, stage anyway with a `note` saying what's unresolved and why.
