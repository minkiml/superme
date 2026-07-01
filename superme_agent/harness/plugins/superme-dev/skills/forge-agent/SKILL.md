---
name: forge-agent
description: Author a complete agent.md from an approved agent proposal, then prove it with the forge_kit lint + behavioural eval. Use when forging an approved agent proposal into its final artifact.
category: learning
access: silent
---

# Forge an agent

You are turning an approved proposal into the **complete agent.md** for a new sub-agent, and proving
it holds up before you stage it. The proposal prompt gives you the spec — `fields`
(`name`, `role`, `tools`, `model`, `trigger`), the `summary` (intent), the `target_scope`, the
publish **slug**, and the **forge_kit path**. Author from the spec; don't invent scope or tools it
doesn't support.

A sub-agent is an **isolated worker**: it runs in its own context and sees only the brief you write
plus the prompt it's handed — never the conversation that spawned it. So the brief must be
self-contained: state its inputs, its single job, and exactly what it returns.

## What makes an agent.md good

- **The `description` is the delegation decision.** It is what a main agent reads to decide whether
  to hand this worker a job. Say what it does and when to delegate to it — concrete enough to route
  correctly, not so broad it grabs the wrong work.
- **`name` is kebab-case and equals the slug** in the prompt — it becomes the on-disk filename.
- **Scope `tools` to the role.** List only the tools the job needs; omitting the field inherits
  everything, which is rarely what you want for a focused worker.
- **`model` must be an alias** — `sonnet`, `opus`, `haiku`, or `inherit` — never a pinned ID like
  `claude-sonnet-4-5`. Pinned IDs go stale or may be invalid; aliases always resolve to the current
  model. If the proposal's `fields.model` carries a pinned/odd value, normalise it to the alias (or
  drop the field to inherit). Lint blocks a non-alias model.
- **Self-contained body.** Because the worker can't see the chat: name its expected input, the steps
  of its single role, and its **return contract** (what shape of result it hands back). If it
  assumes context it isn't given, it will guess.
- **The role only** — no commentary on the pipeline or sibling agents.

## Template to copy

```markdown
---
name: <slug>
description: <what it does and when to delegate to it>.
tools: <only what the role needs, comma-separated>
model: <sonnet|opus|haiku|inherit — an ALIAS only, never a pinned ID; omit unless the role warrants it>
---

<One line naming the worker's single job and the input it receives.>

## How it works

1. <Step.>
2. <Step.>

## Returns

<The exact shape/contents of what it hands back to the caller.>
```

## Procedure

1. **Author** the agent.md from `fields` into a working file in the scratch workspace named in the
   prompt (e.g. `<workspace>/_draft_agent.md`). Write the delegation-clear `description`, scope
   `tools`, and a self-contained body with a return contract.
2. **Lint** — structural check:
   `python <forge_kit>/lint.py agent <workspace>/_draft_agent.md --name <slug>`
   Fix every `ERROR`, weigh each `WARN`, re-run until it prints `PASS`.
3. **Eval** — behavioural check:
   `python <forge_kit>/eval.py agent <workspace>/_draft_agent.md --intent "<summary>"`
   It returns a verdict + per-lens scores + concrete `issues` (each with a fix); high-severity ones
   are usually a missing input or return contract.
4. **Improve** — if the verdict is `fail` OR there are any `high`-severity issues, apply the fixes,
   then re-run lint + eval (step 2→3). Do this **at most once more** (two eval rounds total). `warn`
   with only `low` issues is good to ship.
5. **Stage** — call `mcp__dev__stage_artifact` once with the final agent.md as `content` and the
   **last** eval report JSON (its final line) as `eval_report`, plus an optional one-line `note`.

Do not stage before lint passes. The eval is evidence for the gate-2 reviewer, not a wall — if it
stays `fail` after the improve pass, stage anyway with a `note` saying what's unresolved and why.
