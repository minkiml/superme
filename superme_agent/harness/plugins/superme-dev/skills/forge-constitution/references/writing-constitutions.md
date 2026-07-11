# Authoring standard — constitution

## What it is (and is not)
A unit of SuperMe's operational intelligence — a **convention**, a **small reference**, or a
**contract/schema detail** that shapes how the agent works *for its scope*. **Pulled on demand:** the
`description` sits in the always-on catalog; the body loads only when pulled.

| If it's… | it's not a constitution — it's a… |
|---|---|
| a step-by-step procedure the agent runs | **skill** |
| always-on posture for a whole mode | **charter** |
| something that must be *enforced*, not advised | **hook / tool-scope / permission rule** |
| transient per-task state | **work-item** |

## Classify on two axes — before writing
- **Content shape** — **rule/convention**: the `description` *is* the directive, obeyable from the catalog
  line alone ("Always run the formatter before committing"); body = optional why/example, often omitted.
  **reference/contract**: the `description` says *what it covers + when to pull*; the **body carries the
  substance** (the reference or schema itself).
- **Discovery** — **contextual**: found only implicitly via the catalog — **the default**; the
  `description` must earn the pull on its own. **foundational**: load-bearing, *may* be pointed at
  explicitly from **exactly one** always-on place (rare — e.g. `dev-knowledge-structure`). Default
  contextual.

## Rules
- **One coherent item** — no `and`-joined rules; split into two constitutions instead. *(lint ERROR)*
- **Description is the whole discovery surface** — obeyable (rule) or "what it covers + when to pull"
  (reference); concrete, present tense; **≤1024 chars, no angle brackets**. *(lint ERROR)*
- **Body never restates the description** (or itself); omit it entirely for a self-evident rule.
  *(lint ERROR on a body that only echoes its directive)*
- **Dense and load-bearing** — tables/schema over prose; capture the *shape*, link to migrations/code for
  exact detail; never paste the source of truth.
- **One focused increment** — a single convention / small reference / one contract detail. A full
  expertise pack accretes over many passes; don't author it in one.
- **Portable** — no cwd-relative or hardcoded paths; name other artifacts symbolically (pull-by-name),
  reference plugin files plugin-relative.
- **Right altitude of *why*** — a rationale/reference sharpens the boundary; it doesn't sell the system or
  argue the architecture.
- **Length reflex** — WARN at 200 lines, ERROR at 500.

## Frontmatter
Author only the **`description`** (+ optional body). Publish stamps `name` / `enabled` / `scope` / etc.
A constitution is never executed, so it carries **no `tools` and no `model`**.

## Template
**`<…>` marks a placeholder — replace the whole token; a literal `<>` in the `description` fails lint.**
```
---
description: <the catalog line — for a rule, the directive itself; for a reference, what it covers + when to pull>
---
<body: a rule's optional why/example, OR a reference's actual content. Omit for a self-evident rule.>
```

## Checklist
- [ ] **Right artifact** — a pulled reference/convention, not a skill/charter/hook/work-item?
- [ ] **Both axes named** — rule-or-reference × contextual-or-foundational; defaulted to contextual?
- [ ] **One coherent item** — no `and`-joined rules?
- [ ] **Description is a trigger** — obeyable (rule) or what+when-to-pull (reference); ≤1024, no `< >`?
- [ ] **Body adds substance** — never restates the description or itself; omitted if self-evident?
- [ ] **Portable** — no cwd/hardcoded paths; other artifacts named symbolically?
- [ ] **Altitude / structured / focused / within length** — boundary-why only; tables over prose; one increment?
- [ ] **Discoverable** — a contextual trigger you can simulate, or a single verified pointer if foundational?
- [ ] **lint constitution PASS · eval not `fail`.**
