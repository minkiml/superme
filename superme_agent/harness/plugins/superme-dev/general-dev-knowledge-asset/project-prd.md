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
| 1 | `## Identity` | `- **Who it's for**:` and `- **Why it exists**:` — two bullets, exactly these keys. |
| 2 | `## Goals` | What "good" looks like NOW, specific and checkable. |
| 3 | `## Direction` | Where it goes after that — allowed to be directional, not specific. |
| 4 | `## Non-goals` | Plausible-but-excluded scope, each with a reason. |
| 5 | `## Deliverables` | The FIXED `- **d-<slug>** — Title` set — the spine everything references. |
| 6 | `## Success signals` | One observable outcome per deliverable. |

**These headings are machine-read.** The dashboard's Project view assembles its bands from them by
exact name (`core.dev_knowledge.read_portrait`), so a renamed or missing heading doesn't degrade
gracefully — that band just renders empty and the owner sees a blank page where the project should
be. Keep them verbatim.

## Per-section contract
- **Lede** — `<Project> is a <what> that lets <user> <outcome>.` One plain sentence, directly under the
  H1, **before the first `##`**, no bold (it is read verbatim into the per-turn Orient digest). Hard cap
  one sentence.
- **Identity** — exactly two bullets, keys verbatim:
  `- **Who it's for**: <the people, concretely — "solo devs juggling several repos", not "developers">`
  `- **Why it exists**: <the real reason, not the pitch — what's broken today that makes this worth building>`
  The *why* is the load-bearing line in the whole doc: it's what a reader (human or agent) uses to
  judge whether a proposed change belongs. DON'T: backstory, demographics, personas no deliverable
  touches.
- **Goals** — plain bullets, ≤4. What "good" looks like NOW, stated so you could tell whether it
  happened ("answers *what happened here?* in under a minute, instead of reading git log"). If the
  owner said it in a concrete sentence, use their sentence.
- **Direction** — plain bullets, ≤3, where it goes AFTER the current goals. Allowed to be
  directional — that's the point of splitting it from Goals, so near-term stays sharp without
  losing the longer arc.
- **Deliverables** — a typed record per chunk of value:
  ```markdown
  - **d-<slug>** — Title
    - **Value**: <what the owner can DO once this lands, in their words>
    - **Needs**: <d-ids this depends on, comma-separated — or `none`>
  ```
  The first line is machine-parsed — keep the `**`, the ` — ` em-dash, and the `## Deliverables`
  heading verbatim. `<slug>` is lowercase-hyphenated and **stable forever once minted** — never
  renumber or reuse.
  - **THE VALUE TEST (the one that matters).** A deliverable is a chunk of value the owner can
    RECEIVE, not a component you must build. Write its `Value` first: if you cannot finish *"once
    this lands, I can ___"* without naming another unfinished deliverable, it is **not a
    deliverable** — it's a task or a layer, and it belongs inside one. Plumbing (a CLI shell, a data
    layer, an "orchestration" chunk) is the classic failure: nobody can receive it.
  - **Needs** makes the dependency explicit instead of implied by list order, so the roadmap can't
    quietly present blocked work as parallel. `none` is a real answer and a good sign.
  - **No status, dates, or acceptance criteria** (those live in the roadmap and the items). Retire by
    strike-through with a reason (`~~- **d-x** — Title~~ (retired: superseded by d-y)`) so dangling
    references stay resolvable. >~12 deliverables ⇒ you're conflating deliverables with tasks.
- **Success signals** — a table `| Deliverable | Success signal |`, one row per deliverable, ≤1 line.
  - **Every signal must name a real `d-` id, and every deliverable must have a row.** An orphan on
    either side is a finding, not a formatting slip: a signal with no deliverable means something is
    missing from the list; a deliverable with no signal means nobody can tell when it's done.
  - Prefer an **observable** ("agent orients from docs alone, no code re-scan") over a metric, and a
    THRESHOLD over a restatement — `exits 0 and produces output` is a smoke test, not a success
    signal. If the owner said what "good" looks like ("I get the picture in under a minute instead of
    reading git log"), that sentence IS the signal — use it, don't paraphrase it away.
  - DON'T: aspirational OKRs, revenue/vanity numbers, more than one signal per deliverable.
- **Non-goals** — bullets `- <excluded> — <why out>`, ≤6. Only *plausible-but-excluded* scope, never
  negated goals ("shouldn't be slow") or a wishlist of everything out.

**There is no `## Open questions` section, deliberately.** A question parked in a document blocks
nothing and reminds no one. The owner enters at contracted moments (the gates), so an unknown is
handled one of two ways: settle it in the interview and write the result as a decision, or — if it
surfaces later, mid-flight — the agent DECIDES, records an assumption against the work-item, and the
next gate brief puts it in front of the owner as a confirm/adjust card. An assumption is a question
with a default already applied: reversible, visible, and carrying the cost of being wrong.

During the onboarding interview the rule is narrower still: ask only what the OWNER must decide. A
question you could settle by reading code or researching is not a question — go find out.

## Template
```markdown
# <Project> — project PRD

<Project> is a <what> that lets <user> <outcome>.

## Identity
- **Who it's for**: <the people, concretely>
- **Why it exists**: <what's broken today — the real reason, not the pitch>

## Goals
- <what "good" looks like now, stated so you could tell whether it happened>

## Direction
- <where this goes after the current goals — directional is fine>

## Non-goals
- <plausible-but-excluded thing> — <why out>

## Deliverables
The chunks of intended value. Each `<slug>` is stable; roadmap waves and work-items point at it.

- **d-<slug>** — <Deliverable title>
  - **Value**: <once this lands, the owner can ___>
  - **Needs**: <d-ids, or `none`>

## Success signals
| Deliverable | Success signal |
|-------------|----------------|
| d-<slug>    | <one observable outcome> |
```

## Rules
- **No section is optional.** If one genuinely doesn't apply, write `N/A — <reason>` under its heading
  rather than dropping it, so a reader knows it was *considered, not forgotten*. Silent omission is
  how the sharpest thing the owner said disappears from the doc.
- **Don't paraphrase the owner's own words away.** When they state the pain, the outcome, or what
  "good" looks like in a concrete sentence, that sentence goes in near-verbatim. Your restatement is
  almost always vaguer than what they said.
- **One fact, one home** — never restate the roadmap (sequencing), spec (decisions/why), or architecture
  (how) here; reference `d-<slug>` and let siblings own their facts.
- **Deliverable slugs are immutable** — change a title freely, but never a slug once anything points at
  it; retire by strike-through, never delete.
- **Referential integrity is one-way** — every roadmap wave references a deliverable defined *here*; a
  deliverable need not have any waves yet.
- **No frontmatter.** Clean, readable markdown; tables/tagged-bullets over prose.
