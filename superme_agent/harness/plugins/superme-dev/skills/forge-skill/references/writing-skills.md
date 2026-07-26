# Authoring standard — skill

A skill is a **procedure the agent runs** — a workflow, packaged as a folder (`SKILL.md` + optional
`references/` · `scripts/` · `examples/`). Only its `description` is resident; the body loads on invocation
and runs **in the main context** (unless `context: fork`), auto-invoked by semantic match on its
`description`. So write for that reader: lean, concrete, imperative.

## Resolution order — pick the lightest that holds
`skill (inline) → subagent (separate context) → command (explicit-only)`. A skill is the **default** for a
reusable procedure auto-invoked on intent. Escalate to a subagent only for context isolation, a different
model/tools/permission mode, or true parallel fan-out. *A single inline tool call is a constitution rule,
not a skill.* (Some SuperMe skills — the `forge-*` — are auto-only, never `/`-invoked.)

## Rules
- **Description is the whole routing decision** — the only thing the model sees when choosing.
  `<what it does>. Use when <triggers/keywords/contexts>`, third person, present tense. Include a
  **near-miss** where siblings could collide (the strongest triggering technique). Must carry a **"Use
  when"** clause *(lint WARN without)*; **≤1024 chars, no angle brackets** *(lint ERROR)*.
- **Goals + constraints, not a railroad** — state *what to achieve* and the *rules it must follow*; let a
  capable model choose the *how*. Steps are checkpoints, not a micro-script. Over-dictation makes the skill
  brittle and worse (the model reasons better than it obeys).
- **Only what pushes off the default** — delete any step a competent agent already does; the body's job is
  the SuperMe-specific procedure and the non-obvious *whys*. State each rule **once** — not in the intro,
  a step, *and* a summary.
- **Right altitude of *why*** — a short *decision-boundary* reason beats an ALL-CAPS MUST; but cut
  *system/persuasion* why (don't argue the pipeline's design or sell a rule's merit).
- **Name the tool for an action step** — a step that runs a script/shell command says which tool executes
  it ("run via `Bash`: `python …`"). Implicit invocation lets the model narrate the command as text
  instead of running it.
- **Trust the tool schema** — don't re-document a tool's typed params in prose; for a free-form/JSON param,
  give a concrete example of the shape.
- **No pipeline narration** — instructions for *this* task only.
- **Portable** — reference sibling files **plugin-relative** (`../../…` or `${CLAUDE_PLUGIN_ROOT}`), never
  cwd or an absolute host path.
- **Length reflex** — WARN at 200 lines, ERROR at 500; push bulky detail into `references/`.

## Progressive disclosure (the folder earns its keep)
- **`references/*.md`** — bulky detail (schemas, long specs); point at each with a *when to read it* line so
  it's pulled only if a step earns it.
- **`scripts/*`** — ship deterministic/fragile-parsing work as a CLI so the agent **composes rather than
  reconstructs** boilerplate each run. (Note a bundled script in the stage `note`.)
- **`examples/*`** — concrete exemplars.
- **`agents/`** — skill's own subagents.  
- **Any Others on demand and as necessary** - any other types of skill's sub-folder can be made with relevant contents in it

## Frontmatter
- **Claude Code Native (16):** 

| Field | Type | Required (No: optional) | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Display name and `/slash-command` identifier. Defaults to the directory name if omitted |
| `description` | string | Yes | What the skill does. Shown in autocomplete and used by Claude for auto-discovery |
| `when_to_use` | string | No | Additional context for when Claude should invoke the skill — trigger phrases and example requests. Appended to `description` in the skill listing, counts toward the 1,536-character cap |
| `argument-hint` | string | No | Hint shown during autocomplete (e.g., `[issue-number]`, `[filename]`) |
| `arguments` | string/list | No | Named positional arguments for `$name` substitution in the skill content. Accepts a space-separated string or a YAML list — names map to argument positions in order |
| `disable-model-invocation` | boolean | No | Set `true` to prevent Claude from automatically invoking this skill |
| `user-invocable` | boolean | No | Set `false` to hide from the `/` menu — skill becomes background knowledge only, intended for agent preloading |
| `allowed-tools` | string | No | Tools allowed without permission prompts when this skill is active |
| `disallowed-tools` | string/list | No | Tools removed from Claude's available pool while the skill is active (e.g. block `AskUserQuestion` for a background loop). Accepts a space/comma-separated string or YAML list — the restriction clears on the next message |
| `model` | string | Yes | Model to use when this skill runs (e.g., `haiku`, `sonnet`, `opus`) |
| `effort` | string | No | Override the model effort level when invoked (`low`, `medium`, `high`, `xhigh`, `max`) |
| `context` | string | No | Set to `fork` to run the skill in an isolated subagent context |
| `agent` | string | No | Subagent type when `context: fork` is set (default: `general-purpose`) |
| `hooks` | object | No | Lifecycle hooks scoped to this skill |
| `paths` | string/list | No | Glob patterns that limit when the skill auto-activates. Accepts a comma-separated string or YAML list — Claude loads the skill only when working with matching files |
| `shell` | string | No | Shell for `` !`command` `` blocks — `bash` (default) or `powershell`. Requires `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` |

- **SuperMe-added (catalog only, no native behavior):** `category`, `access`.
- **`model` is an alias** (`sonnet`/`opus`/`haiku`) — never a pinned id *(lint ERROR; resolved at runtime)*.

### Optimize the frontmatter for discoverability

The `name` and `description` in the frontmatter of your `SKILL.md` are the only fields that the agent sees before triggering a skill. If they are not optimized for discoverability and specific enough, your skill is invisible.

* **Adhere to Strict Naming:** The name field must be 1-64 characters, contain only lowercase letters, numbers, and hyphens (no consecutive hyphens), and **must exactly match the parent directory name** (e.g., name: `angular-testing` must live in `angular-testing/SKILL.md`).  
* **Write Trigger-Optimized Descriptions:** (Max 1,024 characters). This is the only metadata the agent sees for routing. Describe the capability in the third person and include "negative triggers."  
  * **Bad:** "React skills." (Too vague).
  * **Good:** "Creates and builds React components using Tailwind CSS. Use when the user wants to update component styles or UI logic. Don't use it for Vue, Svelte, or vanilla CSS projects."

## An example Template
**`<…>` marks a placeholder — replace the whole token; a literal `<>` in the `description` fails lint.**
```markdown
---
name: <kebab-slug>            # = folder name
description: <what it does>. Use when <triggers/keywords>; not for <near-miss → the sibling skill>.
category: <optional>
---

# <Imperative title>

<one line: what this establishes / produces>

## Step 1: <title>
- <imperative instruction>. <the non-obvious *why*, if the step has one.>

## Step 2: <title>
…

## Pitfalls
- <the trap> — <why it bites>
```

## Checklist
- [ ] **Right artifact** — a procedure the agent runs, not knowledge/delegation/posture?
- [ ] **Lightest type** — a skill genuinely beats a subagent/command here?
- [ ] **Description routes** — `<what>. Use when <triggers + near-miss>`; ≤1024, no `< >`?
- [ ] **Body is SuperMe-specific** — no obvious steps; goals-not-railroad; each rule stated once?
- [ ] **Altitude of why** — decision-boundary rationale kept, system/persuasion cut?
- [ ] **Action steps name their tool** — script/shell steps say "run via Bash"; typed params not re-spec'd?
- [ ] **Progressive disclosure** — bulky detail in `references/`, deterministic work in `scripts/`?
- [ ] **Portable / structured / within length** — plugin-relative refs; tables over prose; ≤200-line reflex?
- [ ] **Frontmatter** — `name`=folder, `model`=alias?
- [ ] **lint skill PASS · eval not `fail`.**
