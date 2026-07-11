# Authoring contract — `general/architecture.md`

**What it is.** How the system is **actually built right now** — components, how work flows through them,
the durable data, the system-wide rules, and the load-bearing constraints. Current-state truth, not a
plan and not a rationale log.
**Write / update.** A thin stub at project-init; a **heavy reconstruction at retrofit** (read the code,
write it from what's there); then grow it when a shipped change alters how the system is built.
**Length.** A *cache of understanding*, not a file-by-file tour. Load-bearing only; tables and numbered
flows over prose. Depth scales with the codebase (skip a section rather than pad it).

## Sections (in order)
| # | Section | Holds |
|---|---------|-------|
| 0 | `## Orient` | 2–3 sentence shape + a **"Read before you touch"** list of the highest-leverage rules. |
| 1 | `## Context & externals` | The boundary (who calls in/out) + every external dep and its wiring point. |
| 2 | `## Components` | The spine: one row per load-bearing component. |
| 3 | `## Flows` | The 1–3 canonical paths, each a numbered step-list naming components. |
| 4 | `## Data` | Durable stores + key entities/relationships (incl. on-disk state files). |
| 5 | `## Cross-cutting` | System-wide rules an agent will trip over (auth, errors, config, logging, invariants). |
| 6 | `## Constraints & debt` | Load-bearing "must not" facts + known structural debt, each pointing out. |

Omit any section that doesn't apply (say "stateless" in one line rather than keeping an empty `## Data`).

## Per-section contract
- **Orient** — (a) one sentence what-it-is, (b) 2–3 sentences naming the top 3–5 components, (c) the
  densest thing in the doc: 3–6 bulleted **"Read before you touch"** rules ("all writes go through X;
  never write the DB directly"). ≤12 lines. DON'T restate the PRD's problem.
- **Context & externals** — an externals table `| External | Role | Integration point (file/module) |`
  (~5–15 rows) + a ≤8-line boundary (bullets, or one small diagram). Name the concrete wiring location so
  the agent can jump to code. DON'T list transitive libraries — only externals that shape the design.
- **Components** — the load-bearing table (the doc's spine):
  `| Component | Responsibility (1 line, verb-first) | Location (path) | Depends on |`. 8–25 rows; past
  ~25, split into sub-tables by subsystem (don't nest deeper). Omit a component whose responsibility is
  obvious from its name and has no non-obvious deps. DON'T list every file or restate class internals.
- **Flows** — per flow, a titled numbered list naming the components in sequence
  (`1. Route X → 2. Service Y validates → 3. Spine Z persists`). ≤3 flows, ≤10 steps each. Pick the paths
  that touch the most components. DON'T trace every endpoint.
- **Data** — `| Entity/Table | Purpose | Key fields | Relationships |` (~5–20 rows) or a small ER diagram
  if relationships are dense. Include non-DB durable state that carries meaning (`.assets`, `repos.yaml`).
  Capture the **shape**, never paste DDL/schema (migrations are the source of truth).
- **Cross-cutting** — `<concern> → the rule → where enforced`, 3–7 concerns, phrased as enforceable rules
  ("All API errors return `{error, code}`, thrown via `AppError`"). Omit inapplicable concerns silently
  (no "Caching: N/A" lines).
- **Constraints & debt** — two bullet lists. State each constraint + its **consequence**, not its
  justification. Each debt item ends with a pointer: `→ roadmap` (planned) or `→ spec D-NNN` (rationale).
  This section *references* spec/roadmap, never duplicates them.

## Diagrams
Use one only when the relationship is **non-linear and hard to hold in prose** (a branching flow, a
dependency web, a dense ER graph) — default to a table or numbered list. When you do: **Mermaid** (renders
+ diffs in git), titled, one screen, C4 level 1–2 (context/container). **Never** hand-maintain code-level
diagrams — they rot immediately.

## Template
```markdown
# <Project> — architecture

## Orient
<one sentence: what the system is>. <2–3 sentences: its shape, naming the top components>.

**Read before you touch**
- <highest-leverage rule an agent must not violate>

## Context & externals
<boundary: who calls in / what it calls out — a few bullets>

| External | Role | Integration point |
|----------|------|-------------------|
| <dep>    | <what it's used for> | `<file/module>` |

## Components
| Component | Responsibility | Location | Depends on |
|-----------|----------------|----------|------------|
| <name>    | <verb-first, 1 line> | `<path>` | <components> |

## Flows
### <canonical path name>
1. <component> <does what> → 2. <component> <does what> → …

## Data
| Entity | Purpose | Key fields | Relationships |
|--------|---------|-----------|---------------|
| <name> | <what it stores> | <fields> | <fk / edges> |

## Cross-cutting
- **<concern>** — <the rule> (enforced in `<where>`).

## Constraints & debt
- **Constraint:** <load-bearing fact> — <consequence>.
- **Debt:** <where the structure is knowingly wrong> → roadmap / spec D-NNN.
```

## Rules
- **Current-state only** — describe what's shipped, not what's intended. Reconstruct §2/§3 from the code
  at retrofit; update on ship.
- **Boundaries** — *why we chose X* lives in `spec.md` (link `→ spec D-NNN`); *what's next* lives in
  `roadmap.md`; exact schemas/signatures live in code/migrations (capture shape only). If you're writing a
  rationale or a plan, cut it and link.
- **Location columns everywhere** — the `Location`/`Integration point` paths make the doc a jump table
  into the code; that's the biggest reader affordance.
- **No frontmatter.**
