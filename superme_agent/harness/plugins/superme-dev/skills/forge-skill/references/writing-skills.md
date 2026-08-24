# Authoring standard — skill

A skill is a **procedure the agent runs** — a workflow, packaged as a folder (`SKILL.md` + optional
`references/` · `scripts/` · `examples/`). Only its `description` is resident; the body loads on invocation
and runs **in the main context** (unless `context: fork`), auto-invoked by semantic match on its
`description`. So write for that reader: lean, concrete, imperative.


**Read the writing standard first:** `<forge_kit>/references/principle-for-skills.md` — the run's
prompt names the absolute `forge_kit` path. It owns the writing rules, the 16 frontmatter fields and
the discoverability rules; this file only adds what is specific to SuperMe.

**Write in the workspace's own words.** `../../../references/glossary.md` holds the vocabulary every skill, template and report shares — record vs report, run vs session, receipt, check, bar — with an `Avoid` line on each naming the synonym that will be read as something else. A new artifact that invents its own word for an existing thing is the drift this file exists to stop.

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
- **Number the steps in execution order** — each one an action or a check on an action. Say what to
  do and the rule it must hold to; do not script keystrokes the model already knows. A step that
  writes to a file a later step creates is visible in a numbered list and invisible in prose.
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

The 16 native fields and the discoverability rules are in the writing standard (above) — read it
there rather than from a second copy. Only these are SuperMe's own:

- **SuperMe-added (catalog only, no native behavior):** `category`, `access`.
- **`model` is an alias** (`sonnet`/`opus`/`haiku`) — never a pinned id *(lint ERROR; resolved at runtime)*.

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
- [ ] **Body is SuperMe-specific** — no obvious steps; numbered in execution order; each rule stated once?
- [ ] **Altitude of why** — decision-boundary rationale kept, system/persuasion cut?
- [ ] **Action steps name their tool** — script/shell steps say "run via Bash"; typed params not re-spec'd?
- [ ] **Progressive disclosure** — bulky detail in `references/`, deterministic work in `scripts/`?
- [ ] **Portable / structured / within length** — plugin-relative refs; tables over prose; ≤200-line reflex?
- [ ] **Frontmatter** — `name`=folder, `model`=alias?
- [ ] **lint skill PASS · eval not `fail`.**
