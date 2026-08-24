# A standard prinple in writing skill artifacts (skill and all its packaged files)


## Best practice list
- **Skills are for agents (LLMs), not humans.**

- A SKILL IS A RECIPE. A specialist's procedure card for one repeatable
  job: do this, then this, check that. Not a description of a role, not a
  briefing on the system, not a policy document. 

- INSTRUCT; DO NOT EXPLAIN THE SYSTEM. How the harness fires the run, what
  a gate counts, what the SDK does on spawn — none of it changes what the
  agent does next. Test: if the sentence would still be true with the
  agent removed from the picture, it is not instruction.

- EVERY LINE IS AN ACTION OR A CHECK ON AN ACTION. Anything that is
  neither is commentary. Cut it.

- ONE RULE, ONE HOME. A skill folder holds several kinds of file, and each
  owns exactly one thing:

```
    SKILL.md   — Main skill file the sequence: what to do, in what order

    (the below are optional on demand, when extending to them is improve skill. They are not compulsary and do not unncessarily over-engineer with them)
    
    /reference/  — the method and the bar for one specialised case

    /template/   — organization, strucure, and what the produced artifact should contain and be like.

    /agent/*.md      — how a delegated worker of the skill works and what it hands back (agent declaration with instruction, system prompt, persona, and etc.)

    /scripts/    - Executable code: Deterministic scripts to execute locally using terminal tools to run checks or modify environments during the skill.

    /assets/     - acts as a lazy-loaded storage unit for static files and supporting resources 

  A rule stated in two of them will drift in one, and the reader cannot
  tell which copy is current.
```

- NUMBER THE STEPS IN EXECUTION ORDER, and if the skill ships a
  copy-this checklist, make it match the steps 1:1. Ordering mistakes
  become visible instead of arguable — a step that writes to a file the
  next step creates is obvious in a numbered list and invisible in prose.

- GIVE A REASON ONLY WHERE IT CHANGES THE ACTION, as a clause, never a
  paragraph. Justifying every rule doubles the length and trains the
  reader to skim.

- LABEL EVERY EXAMPLE, AND KEEP IT GENERIC.
  **Good example** / **Bad example** / **Bad and good examples** on the
  line above the fence. Invented names — never this repo's real files, or
  the agent copies the illustration instead of the shape. An unlabelled
  illustration reads as instruction.

- A CHECKABLE TEST BEATS AN EXHORTATION. Give a question with a yes/no
  answer, or a criterion a reader could verify — not "be thorough", "be
  careful", "use good judgment". Those produce no change in behaviour.

- NAME THE EXACT STRING. File paths, tool names, commands, identifiers,
  section headings — verbatim, and only if they actually exist. Verify
  each one before shipping. A paraphrase that silently resolves to
  something else is worse than an error.

- STATE PROHIBITIONS ONLY WHERE THE TEMPTATION IS REAL. Three or four at
  the points the job actually goes wrong. A list of everything not to do
  is a list nobody reads.

- NO DATES, NO INCIDENT LOGS, NO COMMENTS. A skill instructs; it never
  records its own history. A reader who meets a date starts weighing
  whether the rule still applies.

- EACH FILE STANDS ALONE. Write for someone who has read nothing else,
  because in a fresh context that is exactly who arrives.

- DENSE, NOT TERSE. Cut words, keep rules. If removing a sentence loses
  nothing an agent would have done differently, it was never load-bearing.

- DO NOT GO VERBOSE and WORDY. Always write concise and clean words.  

- In addition to above, must read thoroughly the official doc provided by Claude, `https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices`. 

## Official Frontmatter Fields (16) of Claude code 

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Display name and `/slash-command` identifier. Defaults to the directory name if omitted |
| `description` | string | Recommended | What the skill does. Shown in autocomplete and used by Claude for auto-discovery |
| `when_to_use` | string | No | Additional context for when Claude should invoke the skill — trigger phrases and example requests. Appended to `description` in the skill listing, counts toward the 1,536-character cap |
| `argument-hint` | string | No | Hint shown during autocomplete (e.g., `[issue-number]`, `[filename]`) |
| `arguments` | string/list | No | Named positional arguments for `$name` substitution in the skill content. Accepts a space-separated string or a YAML list — names map to argument positions in order |
| `disable-model-invocation` | boolean | No | Set `true` to prevent Claude from automatically invoking this skill |
| `user-invocable` | boolean | No | Set `false` to hide from the `/` menu — skill becomes background knowledge only, intended for agent preloading |
| `allowed-tools` | string | No | Tools allowed without permission prompts when this skill is active |
| `disallowed-tools` | string/list | No | Tools removed from Claude's available pool while the skill is active (e.g. block `AskUserQuestion` for a background loop). Accepts a space/comma-separated string or YAML list — the restriction clears on the next message |
| `model` | string | No | Model to use when this skill runs (e.g., `haiku`, `sonnet`, `opus`) |
| `effort` | string | No | Override the model effort level when invoked (`low`, `medium`, `high`, `xhigh`, `max`) |
| `context` | string | No | Set to `fork` to run the skill in an isolated subagent context |
| `agent` | string | No | Subagent type when `context: fork` is set (default: `general-purpose`) |
| `hooks` | object | No | Lifecycle hooks scoped to this skill |
| `paths` | string/list | No | Glob patterns that limit when the skill auto-activates. Accepts a comma-separated string or YAML list — Claude loads the skill only when working with matching files |
| `shell` | string | No | Shell for `` !`command` `` blocks — `bash` (default) or `powershell`. Requires `CLAUDE_CODE_USE_POWERSHELL_TOOL=1` |


## Optimize the frontmatter for discoverability

The `name` and `description` in the frontmatter of your `SKILL.md` are the only fields that the agent sees before triggering a skill. If they are not optimized for discoverability and specific enough, your skill is invisible.

* **Adhere to Strict Naming:** The name field must be 1-64 characters, contain only lowercase letters, numbers, and hyphens (no consecutive hyphens), and **must exactly match the parent directory name** (e.g., name: `angular-testing` must live in `angular-testing/SKILL.md`).  
* **Write Trigger-Optimized Descriptions:** (Max 1,024 characters). This is the only metadata the agent sees for routing. Describe the capability in the third person and include "negative triggers."  
  * **Bad:** "React skills." (Too vague).
  * **Good:** "Creates and builds React components using Tailwind CSS. Use when the user wants to update component styles or UI logic. Don't use it for Vue, Svelte, or vanilla CSS projects."

## Progressive disclosure and resource management

Maintain a pristine context window by loading information only when needed. **SKILL.md** is the "brain" for high-level logic; offload details to subdirectories.

* **Keep SKILL.md Lean:** Limit the main file to **\<500 lines**. Use it for navigation and primary procedures.  
* **Use Flat Subdirectories:** Move bulky context to standard folders. Keep files exactly **one level deep** (e.g., `references/schema.md`, not `references/db/v1/schema.md`).  
  * `references/`: API docs, cheatsheets, domain logic.  
  * `scripts/`: Executable code for deterministic tasks.  
  * `assets/`: Output templates, JSON schemas, images.  
* **Just-in-Time (JiT) Loading:** Explicitly instruct the agent when to read a file. It will not see these resources until you direct it to (e.g., *"See `references/auth-flow.md` for specific error codes"*).  
* **Explicit Pathing:** Always use **relative paths** with forward slashes (`/`), regardless of the OS.

Skills are for agents, not humans. To keep the context window lean and avoid unnecessary token consumption. **Do not create:**

* **Documentation files:** `README.md`, `CHANGELOG.md`, or `INSTALLATION_GUIDE.md`.  
* **Redundant logic:** If the agent already handles a task reliably without help, delete the instruction.  
* **Library code:** Skills should reference existing tools or contain tiny, single-purpose scripts. Long-lived library code belongs in standard repo CLI directories.

## Use specific procedural instructions instead of prose

Create instructions for LLMs instead of humans.

* **Use Step-by-Step Numbering:** Define the workflow as a strict chronological sequence. If there is a decision tree, map it out clearly (e.g., *"Step 2: If you need source maps run `ng build --source-map`. Otherwise, skip to Step 3."*).  
* **Provide Concrete Templates:** Agents pattern-match exceptionally well. Instead of spending paragraphs describing how a JSON output should look, place a template in the assets/ folder and instruct the agent to copy its structure.  
* **Write in the Third-Person Imperative:** Frame instructions as direct commands to the agent (e.g., *"Extract the text..."* rather than *"I will extract..."* or *"You should extract..."*).

Be specific and consistent in the way you reference concepts in your skill files.

* **Use identical terminology:** Pick a single term to refer to a specific concept.  
* **Specificity**: Use the most specific terminology that’s native to the domain that you describe. For example, in Angular use the concept “template” instead of “html”, “markup”, or “view”.

## Bundle deterministic scripts for repetitive operations

Don't ask the LLM to write complex parsing logic or boilerplate code from scratch every time it runs a skill.

* **Offload fragile/repetitive tasks:** If the agent needs to parse a complex dataset or query a specific database, give it a tested Python, Bash, or Node script to run in the scripts/ directory.  
* **Handle edge cases gracefully:** An agent relies on standard output (stdout/stderr) to know if a script succeeded. Write scripts that return highly descriptive, human-readable error messages so the agent knows exactly how to self-correct without needing user intervention.