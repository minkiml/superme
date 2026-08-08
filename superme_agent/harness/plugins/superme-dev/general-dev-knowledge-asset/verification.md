# Authoring contract — `general/verification.md`

**What it is.** The checks this repo has already PROVEN, kept so the next item inherits them instead
of re-deriving them. The answer to "what does this project always have to keep true, and how do we
show it" — asked by every plan, and by the owner deciding what the exam is worth.

**Write / update.** **You do not author this doc.** It is the one anchor doc nothing writes by hand:

| act | who | when |
|---|---|---|
| nominate | vet (`nominate_check`) | after a check has actually PASSED, and only if it defends the repo rather than the item |
| write | close (`apply_knowledge_delta`, doc `verification`, section `Available`) | at close, from that item's nominations |
| promote to standing | **the owner only** | in the dashboard — Artifacts → Verification |
| drop | the owner | when an entry turns out not to generalise |

**Length.** However many entries the repo has earned. A fresh project's is EMPTY, and that is the
correct state — onboarding must not seed it.

## The one rule that makes this doc worth having
**Every entry has run and come back green here.** A library of untested hypotheses is worse than no
library: the next plan inherits one, it doesn't work, and that item spends a whole cycle discovering
what this one could have told it. The nomination tool enforces it — a check with no passing verdict
is refused.

The second rule follows from the first: **an entry describes the REPO, never the item that proved
it.** No `covers:`, no task ids, no work-item ids. The item will be closed and gone by the time
anything inherits this; a reference to it is a pointer into nothing. The write refuses these too.

## Sections
| # | Section | Holds |
|---|---------|-------|
| 1 | `## Standing` | Attached to EVERY implementation plan in this repo, by the kernel, at scaffold time. |
| 2 | `## Available` | Cited by name from a plan when it fits. Where a nomination lands. |

The difference is who pays. An available entry costs nothing until a plan chooses it. A standing
entry taxes every future item in this repo, forever — which is why promoting is the owner's call and
nobody else's. It is the one brake on the library quietly accreting into a tax nobody agreed to.

Prose under either heading is yours: a line on why something is standing survives promote/demote/drop.

## Per-entry contract
An entry is a verification-plan check, in exactly the grammar `plan.md` uses — so inheriting one is a
copy, not a translation:

```markdown
### suite-green
- proves: nothing that already worked in this project stopped working
- traces: every deliverable depends on the suite staying green
- mode: command
- scenario: run the project suite from the repo root
- run: python -m pytest -q
- expect: exit 0 with no failures
```

- **id** — a lowercase slug, and the join key into every item's evidence ledger. Name the property,
  not the mechanism: `migrations-reversible` beats `run-migrate-down`.
- **proves** — one plain sentence: what is true of the product when this passes, in the owner's
  terms. It is the line the reports and the Proof view show, so it must read with the rest of the
  block covered — "nothing that already worked stopped working", never "exit code is 0".
- **traces** — what about this repo the check defends. If you can only say it by naming a work-item,
  it is not a library entry.
- **mode · scenario · run · expect · rubric** — the same fields and the same bars as any check
  (`references/artifacts.md` § "plan.md — the section contract"). `run:` is worth having here more
  than anywhere else: an inherited check with a run block costs the next item nothing at all.

## What does NOT go here
- A check that has not passed. Nominate it after it does.
- A check about one item's feature. That belongs in that item's plan and dies with it.
- A convention with no way to fail ("the code should be clean"). An entry that cannot come back red
  is a sentence, not a check.
- Prose about the project's testing philosophy. That is `architecture.md`.
