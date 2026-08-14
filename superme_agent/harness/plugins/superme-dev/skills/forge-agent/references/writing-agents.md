# Authoring standard — subagent

A subagent is an **isolated-context autonomous worker**: it runs in its own context window, does one scoped
job unguided, and **returns only its conclusion** to the caller — it sees the brief you write plus the
prompt it's handed, never the conversation that spawned it. So the brief must stand alone.


**Write in the workspace's own words.** `../../references/glossary.md` holds the vocabulary every skill, template and report shares — record vs report, run vs session, receipt, check, bar — with an `Avoid` line on each naming the synonym that will be read as something else. A new artifact that invents its own word for an existing thing is the drift this file exists to stop.

## The self-contained skeleton (the heart of it)
The worker has no ambient context, so the prompt must stand alone. The recurring skeleton:
1. **Role line** — "You are a specialized X agent that …".
2. **`## Task` / Execution Contract** — the one job + the hard forbiddens.
3. **`## Inputs`** — name what the caller supplies (`repo_root`, `baseline_ref`, the proposal `fields`, …).
   The worker can't see the conversation, so **an unstated input does not exist**. This is the most-missed
   section — omitting it is why a worker guesses at data it was actually handed.
4. **`## Workflow`** — numbered deterministic steps.
5. **`## Return Format`** — a **literal fenced template + enums** the caller parses. Implicit return shape
   makes the worker guess its own output.
6. **`## Critical Rules`** — numbered do/don't reinforcing the contract.

Every subagent **names its inputs** and **fences its return**. If the caller would have to guess what it
gets back, the contract is missing.

## Rules
- **Description is the delegation decision** — *what it does + when to delegate*, concrete enough to route
  right, not so broad it grabs the wrong work. Add **`PROACTIVELY`** to encourage auto-delegation. The
  **method belongs in the body**, not the description. Needs a "Use when"/PROACTIVELY trigger *(lint WARN
  without)*; **≤1024 chars, no angle brackets** *(lint ERROR)*.
- **Scope `tools` to the role** — omitting `tools` inherits the *entire* pool and defeats isolation. The
  **negative space of the allowlist is a guardrail**: exclude what would let the worker bypass its own
  design (a fetch-via-skill worker withholds `WebFetch` so it *can't* route around the skill).
- **Bound in prose what frontmatter can't restrict** — `Bash`/`Write` can't be path-scoped, so say it:
  "scratch file only, never the artifact's real home."
- **Single responsibility**, stated as such ("you do one thing: read, decide, file").
- **Trust the tool schema — don't re-spec typed params.** Re-state a param only to add what the schema
  *can't* carry (a cross-field rule, or an enum the tool takes as free text). For a **free-form/JSON** param
  (typed only as object/string), give a **concrete example of the exact shape** — the one place the model
  genuinely guesses.
- **State at point of use** — call a read tool in the step that *consumes* its result; don't fetch in step 1
  and ask the worker to hold data across heavy reasoning (held state frays).
- **Stateful workers: define the contradiction case** — if the worker merges into prior state, say what to
  do when new input *contradicts* it: **override and note the change**, not only when it's additive.
- **`model` alias**, matched to weight — `haiku` mechanical/deterministic, `sonnet` judgment. Never a pinned
  id *(lint ERROR; resolved at runtime)*.
- **Portable / length** — no cwd/hardcoded paths; inputs symbolic. WARN at 200 lines, ERROR at 500.

## Frontmatter

| Field | Type | Required (No: Optional) | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Unique identifier using lowercase letters and hyphens |
| `description` | string | Yes | When to invoke. Use `"PROACTIVELY"` for auto-invocation by Claude |
| `tools` | string/list | No | Comma-separated allowlist of tools (e.g., `Read, Write, Edit, Bash`). **Inherits all tools if omitted**. Supports `Agent(agent_type)` syntax to restrict spawnable subagents; the older `Task(agent_type)` alias still works |
| `disallowedTools` | string/list | No | Tools to deny, removed from inherited or specified list |
| `model` | string | Yes | Model to use: `sonnet`, `opus`, `haiku`, a full model ID (e.g., `claude-opus-4-6`), or `inherit` (default: `inherit`) |
| `permissionMode` | string | No | Permission mode: `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, or `plan` |
| `maxTurns` | integer | No | Maximum number of agentic turns before the subagent stops |
| `skills` | list | No | Skill names to preload into agent context at startup (full content injected, not just made available) |
| `mcpServers` | list | No | MCP servers for this subagent — server name strings or inline `{name: config}` objects |
| `hooks` | object | No | Lifecycle hooks scoped to this subagent. All hook events are supported; `PreToolUse`, `PostToolUse`, and `Stop` are the most common |
| `memory` | string | No | Persistent memory scope: `user`, `project`, or `local` |
| `background` | boolean | No | Set to `true` to always run as a background task (default: `false`) |
| `effort` | string | Yes | Effort level override when this subagent is active: `low`, `medium`, `high`, `xhigh`, `max` (Opus 4.6 only). Default: inherits from session |
| `isolation` | string | No | Set to `"worktree"` to run in a temporary git worktree (auto-cleaned if no changes) |
| `initialPrompt` | string | No | Auto-submitted as the first user turn when this agent runs as the main session agent (via `--agent` or the `agent` setting). Commands and skills are processed. Prepended to any user-provided prompt |
| `color` | string | No | Display color for the subagent in the task list and transcript: `red`, `blue`, `green`, `yellow`, `purple`, `orange`, `pink`, or `cyan` |


- **SuperMe-added (catalog only, no native behavior):** `category`
- **SuperMe overrides the native table on two points:** (1) `model` must be an **alias**
  (`sonnet`/`opus`/`haiku`/`inherit`) — a pinned id is a **lint ERROR** (SuperMe resolves the alias→concrete
  at runtime); (2) lint requires only `name` + `description` — `model`/`effort`/`maxTurns` are optional
  (`effort` defaults to `medium`). `maxTurns` is honored by the SDK, not by SuperMe's own code.


## An example Template
**`<…>` marks a placeholder — replace the whole token, brackets included.** A literal `<>` in `description`
is a lint ERROR, and a copied `name: <slug>` fails the name-matches-slug check.
```markdown
---
name: <kebab-slug>              # = filename
description: <what it does>. Use this agent PROACTIVELY when <when to delegate>.
tools: Read, Grep, Bash         # scope to the role — omitting inherits EVERY tool
model: haiku                    # alias, matched to weight (haiku mechanical · sonnet judgment)
---

You are a specialized <X> agent that <one job, one sentence>.

## Task
<the single job> — <the hard forbiddens>.

## Inputs (provided by the caller)
- <name>: <what it is; the worker can't see the conversation, so name everything>

## Workflow
1. <step> → 2. <step> → …

## Return Format
​```
<a literal template the caller parses, with enums, e.g. verdict: clean|dirty>
​```

## Critical Rules
1. <do / don't that reinforces the contract>
```

## Worked example — `forge` (a live model)
A `Read, Grep, Write, Bash, Skill, mcp__dev__stage_artifact` worker: it names its inputs in prose (the
proposal's `output_form`, `fields`, slug, forge_kit path), does one job (author → validate → stage one
proposal), fences its return, and — where the allowlist can't path-restrict `Bash`/`Write` — **bounds it in
prose**: "never write to the artifact's real home." Study it for the self-contained brief + prose guardrail.
`distill` is the model for a **fenced return + a typed-JSON `fields` example** per output form.

## Checklist
- [ ] **Right artifact** — isolated autonomous work, not an in-context procedure/knowledge?
- [ ] **Justified isolation** — protects main context / adds compute / needs different model-tools; not a one-liner?
- [ ] **Description routes** — what + when to delegate (PROACTIVELY); method in body; ≤1024, no `< >`?
- [ ] **Self-contained** — role · Task · **Inputs named** · Workflow · **fenced Return** · Critical Rules?
- [ ] **Tools scoped** — allowlist enforces the design; un-restrictable tools bounded in prose; single responsibility?
- [ ] **Params not re-spec'd** — typed params trusted to the schema; free-form/JSON params get an example shape?
- [ ] **Reads at point of use** — nothing fetched early and held; contradiction case defined for stateful workers?
- [ ] **Model matched** — `model` an alias (`sonnet`/`opus`/`haiku`/`inherit`) matched to the role's weight?
- [ ] **Altitude of why / portable / structured / within length** — boundary-why only; no cwd paths; ≤200-line reflex?
- [ ] **lint agent PASS · eval not `fail`.**
