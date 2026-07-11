# Authoring contract — `general/project-prd.md`

**What it is.** The durable answer to "what are we building and **why**," broken into stable
**deliverables** the roadmap and work-items point at. A cache of intent, not a spec dump.
**Write / update.** Draft at onboarding; update when scope shifts or a deliverable is added/retired.
**Length.** Tight — tables and tagged bullets over prose. Every fact appears **once**, in the section
that owns it (no cross-section restatement — that's what keeps it fast to read and clean to update).

## Sections (in order)
| # | Section | Holds |
|---|---------|-------|
| — | **Lede** (no heading) | One plain sentence: what this project is. The always-on Orient line. |
| 1 | `## Problem & why` | The pain (a concrete scenario) + evidence it's real. |
| 2 | `## Users` | The 1–3 personas the deliverables serve (the agent itself may be one). |
| 3 | `## Deliverables` | The FIXED `- **d-<slug>** — Title` set — the spine everything references. |
| 4 | `## Success signals` | One observable outcome per deliverable. |
| 5 | `## Non-goals` | Plausible-but-excluded scope, each with a reason. |
| 6 | `## Open questions` | Live unknowns, each tagged `[OPEN]`/`[RESOLVED: …]`. |

## Per-section contract
- **Lede** — `<Project> is a <what> that lets <user> <outcome>.` One plain sentence, directly under the
  H1, **before the first `##`**, no bold (it is read verbatim into the per-turn Orient digest). Hard cap
  one sentence.
- **Problem & why** — ≤4 bullets, ≤1 line each. Lead bullet states the pain as a concrete scenario; the
  rest are evidence (a fact / observed behavior, not opinion). DON'T: backstory, hedging.
- **Users** — a table `| Persona | Their need |`, ≤3 rows. DON'T: demographics, journey maps, personas no
  deliverable touches.
- **Deliverables** — bullets, EXACT `- **d-<slug>** — Title` (this line is machine-parsed — keep the
  `**`, the ` — ` em-dash, and the `## Deliverables` heading verbatim). `<slug>` is lowercase-hyphenated
  and **stable forever once minted** — never renumber or reuse. Order by logical dependency, not priority
  (priority lives in the roadmap). One line each; **no status, dates, or acceptance criteria**. Retire by
  strike-through with a reason (`~~- **d-x** — Title~~ (retired: superseded by d-y)`) so dangling
  references stay resolvable. >~12 deliverables ⇒ you're conflating deliverables with tasks.
- **Success signals** — a table `| Deliverable | Success signal |`, one row per deliverable, ≤1 line.
  Prefer an **observable** ("agent orients from docs alone, no code re-scan") over a metric. DON'T:
  aspirational OKRs, revenue/vanity numbers, more than one signal per deliverable.
- **Non-goals** — bullets `- <excluded> — <why out>`, ≤6. Only *plausible-but-excluded* scope, never
  negated goals ("shouldn't be slow") or a wishlist of everything out.
- **Open questions** — bullets ending in a state tag `- <question> — [OPEN | RESOLVED: <answer>]`.
  Resolve in place; prune resolved items at the next phase boundary. If it grows past ~6, the items are
  really work — push them to the roadmap/inbox.

## Template
```markdown
# <Project> — project PRD

<Project> is a <what> that lets <user> <outcome>.

## Problem & why
- <the pain, as a concrete scenario>
- <evidence it's real — a fact or observed behavior>

## Users
| Persona | Their need |
|---------|-----------|
| <who>   | <the one need that matters here> |

## Deliverables
The chunks of intended value. Each `<slug>` is stable; roadmap waves and work-items point at it.

- **d-<slug>** — <Deliverable title>

## Success signals
| Deliverable | Success signal |
|-------------|----------------|
| d-<slug>    | <one observable outcome> |

## Non-goals
- <plausible-but-excluded thing> — <why out>

## Open questions
- <unresolved decision> — [OPEN]
```

## Rules
- **One fact, one home** — never restate the roadmap (sequencing), spec (decisions/why), or architecture
  (how) here; reference `d-<slug>` and let siblings own their facts.
- **Deliverable slugs are immutable** — change a title freely, but never a slug once anything points at
  it; retire by strike-through, never delete.
- **Referential integrity is one-way** — every roadmap wave references a deliverable defined *here*; a
  deliverable need not have any waves yet.
- **No frontmatter.** Clean, readable markdown; tables/tagged-bullets over prose.
