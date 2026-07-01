---
name: forge-constitution
description: Author a constitution statement from an approved constitution proposal, then prove it with the forge_kit lint + behavioural eval. Use when forging an approved constitution proposal into its final artifact.
category: learning
access: silent
---

# Forge a constitution item

You are turning an approved proposal into a **constitution statement** — one always-on directive
injected into the agent's system prompt every turn for its scope. The proposal prompt gives you the
spec — `fields` (`statement`, `scope`, `rationale`), the `summary` (intent), the `target_scope`, and
the **forge_kit path**.

A constitution item is a convention or guardrail the agent should *always* follow — the way a code
convention or a house rule is always in force. It is **not** a procedure (that's a skill) and not a
fact to look up (that's knowledge). It earns its place by changing behaviour on every turn.

## What makes a constitution statement good

- **A bare statement — no frontmatter.** Write only the directive itself; the publish step wraps it
  with `enabled`/scope/etc. (Lint will reject any frontmatter you add.)
- **One directive, not several.** If you're tempted to use "and" to join unrelated rules, that's two
  items, or it's really a skill. Keep it to a single clear rule.
- **Imperative and concrete.** "Always run the formatter before committing" — not "formatting is
  important". The agent should be able to act on it without interpretation.
- **Short.** A sentence or two. If it needs a paragraph of steps, it's a skill in disguise.
- **No conflict.** It must sit alongside the rules already in force, not contradict or duplicate one.

## Template to copy

```
<A single always-on directive, stated imperatively and concretely.>
```

That's the whole artifact — one statement, nothing else.

## Procedure

1. **Author** the statement from `fields.statement` (sharpened) into a working file in the scratch
   workspace named in the prompt (e.g. `<workspace>/_draft_constitution.md`) — bare text, no
   frontmatter.
2. **Lint** — structural check:
   `python <forge_kit>/lint.py constitution <workspace>/_draft_constitution.md`
   Fix every `ERROR` (most commonly: you left frontmatter in, or it's an essay not a directive),
   weigh each `WARN`, re-run until it prints `PASS`.
3. **Eval** — behavioural check for clarity + conflict:
   `python <forge_kit>/eval.py constitution <workspace>/_draft_constitution.md --intent "<summary>"`
   (If the prompt names an `existing rules` file for this scope, add `--existing <that path>` so the
   conflict check is real.) It returns a verdict + per-lens scores + concrete `issues`.
4. **Improve** — if the verdict is `fail` OR there are any `high`-severity issues, tighten the
   statement (split a bundled rule, sharpen vague wording, resolve a clash) and re-run lint + eval.
   Do this **at most once more** (two eval rounds total). `warn` with only `low` issues is good to
   ship.
5. **Stage** — call `mcp__dev__stage_artifact` once with the final statement as `content` and the
   **last** eval report JSON (its final line) as `eval_report`, plus an optional one-line `note`.

Do not stage before lint passes. The eval is evidence for the gate-2 reviewer, not a wall — if it
stays `fail` after the improve pass, stage anyway with a `note` saying what's unresolved and why.
