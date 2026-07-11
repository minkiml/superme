# Authoring contract — `general/spec.md`

**What it is.** How the project is built at the **decision level** — the stack, the overall approach, and
the key technical decisions **with their reasoning and rejected alternatives**. The durable answer to
"what did we choose, and **why**." The slow-drift doc: it changes only when a real decision is made or
reversed.
**Write / update.** Draft stack + approach at onboarding; append a decision entry whenever a load-bearing
choice is made.
**Length.** Decisions, not documentation. The preamble stays tiny; the **decision log is the only section
that grows.** Skip anything obvious from the code.

## Sections (in order)
| # | Section | Holds |
|---|---------|-------|
| 1 | `## Stack` | The chosen technologies — fixed facts, one line each. |
| 2 | `## Approach` | The 3–6 sentence shape of *how* it's built; the mental model before any decision. |
| 3 | `## Constraints` | Hard boundaries that pre-decide future choices (perf, compat, security, "must not"). |
| 4 | `## Key decisions` | The **append-only decision log** — the heart of the doc. |

Current-state structure (components/data flow) is **not here** — it's `architecture.md`. Plans/waves are
`roadmap.md`. Product what/why is `project-prd.md`. Don't recreate them.

## Per-section contract
- **Stack** — flat bullets `- **<area>**: <choice> — <one clause of why, only if non-obvious>`. No prose.
  If the "why" is a real trade-off, it's not a stack line — it's a decision (link `see D-012`).
- **Approach** — one tight paragraph (≤6 sentences) or 3–5 bullets: the overall strategy and the
  load-bearing pattern ("event-sourced core, thin surface adapters"). No component inventory (that's
  architecture). If a sentence contains "because we rejected X," move it to a decision.
- **Constraints** — bulleted imperatives (`- All FS writes stay home-bounded`, `- p95 API < 200ms`). A
  constraint whose reasoning is contested graduates into a decision entry.
- **Key decisions** — one atomic block per decision, using the **exact entry format** below. Two agents
  must produce identically-shaped entries.

### Decision entry format
```markdown
### D-042 · <imperative decision title> · accepted
- **Date**: <YYYY-MM-DD>
- **Decision**: <what we chose — one declarative present-tense sentence>
- **Why**: <the forcing context + rationale — 1–3 sentences>
- **Rejected**: <alternative> — <the one reason it lost>
```
- **ID** `D-NNN`, zero-padded, monotonic, **never reused** — it's the grep anchor + supersession target.
- **Status is inline in the heading** so scanning `### D-` lines shows state without opening the body.
  Only `accepted` or `superseded by D-NNN` (no `proposed` — a spec records *settled* choices).
- **Fixed, ordered fields**: Date · Decision · Why · Rejected(×N). `Why` is mandatory (no why ⇒ it's a
  Stack line). ≥1 `Rejected` unless none existed (`- **Rejected**: none — no viable alternative`).
- **One decision per entry** (no "and also"); reference others inline (`supersedes D-030`).

## When to write vs leave it
Write a decision when the choice (a) is expensive to reverse, (b) has a real alternative someone would
reasonably pick, or (c) will make a future agent ask "wait, why this way?". Otherwise it's
obvious-from-code — leave it out. Don't log coding style, code-evident facts, or still-under-debate items.

## Reversing a decision (append-only — never edit or delete a decision's body)
1. Add a **new** entry `D-071` for the new choice, with `supersedes D-042` in its **Why**.
2. Edit **only** the old entry's status line → `### D-042 · … · superseded by D-071`. Its body stays 100%
   intact — the original *why* is the preserved reasoning trail.
3. Update **both** sides (forward `superseded by` + back-ref `supersedes`). Updating one side only is the
   #1 rot — a stale decision with no forward pointer makes a reader act on a reversed choice.

## Template
```markdown
# <Project> — spec

## Stack
- **<area>**: <choice> — <one clause of why, if non-obvious>

## Approach
<how it fits together and the load-bearing pattern, ≤6 sentences>

## Constraints
- <hard limit that pre-decides future choices>

## Key decisions
### D-001 · <decision title> · accepted
- **Date**: <YYYY-MM-DD>
- **Decision**: <what we chose>
- **Why**: <forcing context + rationale>
- **Rejected**: <alternative> — <why it lost>
```

## Rules
- **Capture the *why* and the *rejected alternative*** — a decision without them is a Stack line or noise;
  a rejected alternative without a reason gets re-proposed.
- **No future tense except `superseded by`** — plans/TODOs go to the roadmap; current structure goes to
  architecture. If you're describing code, cut it.
- **Keep entries greppable** — uniform `### D-NNN · title · status` headings and fixed `**Why**`/
  `**Rejected**` labels so an agent can list decisions or pull every rejected alternative with one grep.
- **No frontmatter.**
