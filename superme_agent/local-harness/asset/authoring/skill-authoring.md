---
name: skill-authoring
description: The bar for a skill and its bundled files: recipe shape, frontmatter, progressive disclosure. Pull before writing or reviewing any SKILL.md.
enabled: true
hub-only: true
---

# A standard principle in writing skill artifacts

Covers the skill file and every file packaged alongside it.

## The bar

- **Skills are for agents (LLMs), not humans.**

- **A skill is a recipe.** A specialist's procedure card for one repeatable job: do this, then
  this, check that. Not a description of a role, not a briefing on the system, not a policy
  document.

- **Instruct; do not explain the system.** How the harness fires the run, what a gate counts, what
  the SDK does on spawn — none of it changes what the agent does next. Test: if the sentence would
  still be true with the agent removed from the picture, it is not instruction.

- **Every line is an action or a check on an action.** Anything that is neither is commentary.
  Cut it.

- **One rule, one home.** A skill folder holds several kinds of file, and each owns exactly one
  thing. A rule stated in two of them will drift in one, and the reader cannot tell which copy is
  current.

  | file | owns |
  |---|---|
  | `SKILL.md` | the sequence: what to do, in what order |
  | `references/` | the method and the bar for one specialised case |
  | `templates/` | the structure and contents the produced artifact should have |
  | `agents/*.md` | how a delegated worker runs and what it hands back |
  | `scripts/` | deterministic code run locally to check or modify the environment |
  | `assets/` | lazy-loaded static files and supporting resources |

  Everything below `SKILL.md` is optional. Add one only where it makes the skill better; do not
  over-engineer with them.

- **Number the steps in execution order**, and if the skill ships a copy-this checklist, make it
  match the steps 1:1. Ordering mistakes become visible instead of arguable — a step that writes to
  a file the next step creates is obvious in a numbered list and invisible in prose.

- **Give a reason only where it changes the action**, as a clause, never a paragraph. Justifying
  every rule doubles the length and trains the reader to skim.

- **Label every example, and keep it generic.** Put **Good example** / **Bad example** / **Bad and
  good examples** on the line above the fence. Use invented names, never this repo's real files, or
  the agent copies the illustration instead of the shape. An unlabelled illustration reads as
  instruction.

- **A checkable test beats an exhortation.** Give a question with a yes/no answer, or a criterion a
  reader could verify — not "be thorough", "be careful", "use good judgment". Those produce no
  change in behaviour.

- **Name the exact string.** File paths, tool names, commands, identifiers, section headings —
  verbatim, and only if they actually exist. Verify each one before shipping. A paraphrase that
  silently resolves to something else is worse than an error.

- **State prohibitions only where the temptation is real.** Three or four, at the points the job
  actually goes wrong. A list of everything not to do is a list nobody reads.

- **No dates, no incident logs, no comments.** A skill instructs; it never records its own history.
  A reader who meets a date starts weighing whether the rule still applies.

- **Each file stands alone.** Write for someone who has read nothing else, because in a fresh
  context that is exactly who arrives.

- **Dense, not terse.** Cut words, keep rules. If removing a sentence loses nothing an agent would
  have done differently, it was never load-bearing.

- **Do not go verbose or wordy.** Always write concise, clean words.

- Read the official Claude guidance in full:
  `https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices`

## Official frontmatter fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Display name and `/slash-command` identifier. Defaults to the directory name if omitted |
| `description` | string | Recommended | What the skill does. Shown in autocomplete and used by Claude for auto-discovery |
| `when_to_use` | string | No | Additional context for when Claude should invoke the skill — trigger phrases and example requests. Appended to `description` in the skill listing, counts toward the 1,536-character cap |
| `argument-hint` | string | No | Hint shown during autocomplete (e.g. `[issue-number]`, `[filename]`) |
| `arguments` | string/list | No | Named positional arguments for `$name` substitution in the skill content. Accepts a space-separated string or a YAML list — names map to argument positions in order |
| `disable-model-invocation` | boolean | No | Set `true` to prevent Claude from automatically invoking this skill |
| `user-invocable` | boolean | No | Set `false` to hide from the `/` menu — the skill becomes background knowledge only, intended for agent preloading |
| `allowed-tools` | string | No | Tools allowed without permission prompts when this skill is active |
| `disallowed-tools` | string/list | No | Tools removed from Claude's available pool while the skill is active (e.g. block `AskUserQuestion` for a background loop). Accepts a space/comma-separated string or YAML list — the restriction clears on the next message |
| `model` | string | No | Model to use when this skill runs (e.g. `haiku`, `sonnet`, `opus`) |
| `effort` | string | No | Override the model effort level when invoked (`low`, `medium`, `high`, `xhigh`, `max`) |
| `context` | string | No | Set to `fork` to run the skill in an isolated subagent context |
| `agent` | string | No | Subagent type when `context: fork` is set (default: `general-purpose`) |
| `hooks` | object | No | Lifecycle hooks scoped to this skill |
| `paths` | string/list | No | Glob patterns that limit when the skill auto-activates. Accepts a comma-separated string or YAML list — Claude loads the skill only when working with matching files |
| `shell` | string | No | Shell for `` !`command` `` blocks — `bash` (default) or `powershell`. Requires `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` |

## Optimize the frontmatter for discoverability

`name` and `description` are the only fields the agent sees before triggering a skill. If they are
not specific and not optimized for discovery, the skill is invisible.

- **Adhere to strict naming.** `name` must be 1–64 characters, lowercase letters, numbers and
  hyphens only, no consecutive hyphens, and it **must exactly match the parent directory name**
  (`name: angular-testing` lives in `angular-testing/SKILL.md`).
- **Write trigger-optimized descriptions** (max 1,024 characters). This is the only metadata the
  agent routes on. Describe the capability in the third person and include negative triggers.

**Bad example**
```yaml
description: React skills.
```

**Good example**
```yaml
description: Creates and builds React components using Tailwind CSS. Use when the user wants to update component styles or UI logic. Don't use it for Vue, Svelte, or vanilla CSS projects.
```

## Progressive disclosure and resource management

Keep the context window pristine by loading information only when it is needed. `SKILL.md` is the
brain for high-level logic; offload detail to subdirectories.

- **Keep `SKILL.md` lean.** Limit the main file to **under 500 lines**. Use it for navigation and
  primary procedures.
- **Use flat subdirectories.** Keep files exactly **one level deep** (`references/schema.md`, not
  `references/db/v1/schema.md`).
  - `references/`: API docs, cheatsheets, domain logic.
  - `scripts/`: executable code for deterministic tasks.
  - `assets/`: output templates, JSON schemas, images.
- **Load just in time.** Explicitly tell the agent when to read a file — it will not see these
  resources until directed (e.g. *"See `references/auth-flow.md` for specific error codes"*).
- **Use explicit relative paths** with forward slashes, on every OS.

To keep the context window lean, **do not create**:

- **Documentation files:** `README.md`, `CHANGELOG.md`, `INSTALLATION_GUIDE.md`.
- **Redundant logic:** if the agent already handles a task reliably without help, delete the
  instruction.
- **Library code:** skills reference existing tools or contain tiny, single-purpose scripts.
  Long-lived library code belongs in the repo's standard CLI directories.

## Use specific procedural instructions instead of prose

- **Number the workflow** as a strict chronological sequence. Map any decision tree explicitly
  (e.g. *"Step 2: if you need source maps run `ng build --source-map`. Otherwise skip to Step 3."*).
- **Provide concrete templates.** Agents pattern-match exceptionally well. Rather than describing
  how a JSON output should look, put a template in `assets/` and tell the agent to copy its
  structure.
- **Write in the third-person imperative.** *"Extract the text…"*, not *"I will extract…"* or
  *"You should extract…"*.
- **Use identical terminology.** Pick one term per concept and keep it.
- **Be specific.** Use the most specific term native to the domain — in Angular, "template", not
  "html", "markup" or "view".

## Bundle deterministic scripts for repetitive operations

Do not ask the LLM to write complex parsing logic or boilerplate from scratch on every run.

- **Offload fragile or repetitive tasks.** If the agent must parse a complex dataset or query a
  specific database, give it a tested Python, Bash or Node script in `scripts/`.
- **Handle edge cases gracefully.** An agent reads stdout and stderr to know whether a script
  succeeded. Write highly descriptive, readable error messages so it can self-correct without the
  user.
