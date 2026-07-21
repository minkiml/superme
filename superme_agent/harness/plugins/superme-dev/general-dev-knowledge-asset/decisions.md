# Authoring contract — `general/decisions.md`

**What it is.** The **append-only ledger** of load-bearing choices: what we chose, why, and what we
rejected. The durable answer to *"wait, why is it done this way?"* — asked by a future agent or a
future you.
**Write / update.** Append an entry the moment a load-bearing choice is settled. Never edit a past
entry's body; reverse by appending a new one (see below).
**Length.** Unbounded by design — this is the one doc that only grows. Keep it small the honest way:
by *not writing* entries that don't earn their place (see "When to write").

Why it isn't part of `spec.md`: a decision record is immutable **history**; spec's stack, approach and
constraints are mutable **current-state truth**. One file holding both grows without bound and buries
the part you read to orient. `roadmap.md` (forward-only) and `architecture.md` (current-state) are
already split this way.

## Sections (in order)
| # | Section | Holds |
|---|---------|-------|
| 1 | `## Decisions` | The entries, **newest last** (append-only, so the file reads chronologically). |

That's the whole doc. No preamble, no index — the entry headings ARE the index (`grep '^### D-'`).

## Entry format
```markdown
### D-042 · <imperative decision title> · accepted
- **Date**: <YYYY-MM-DD>
- **Decision**: <what we chose — one declarative present-tense sentence>
- **Why**: <the forcing context + rationale — 1–3 sentences>
- **Rejected**: <alternative> — <the one reason it lost>
```

- **ID** `D-NNN`, zero-padded, monotonic, **never reused** — it's the grep anchor and the
  supersession target.
- **Status is inline in the heading** so scanning `### D-` lines shows state without opening bodies.
  Three values only:
  - `accepted` — in force.
  - `superseded by D-NNN` — replaced by a later decision.
  - `deprecated` — no longer relevant (the thing it governed is gone), but kept for history.

  No `proposed` — this ledger records **settled** choices. A choice still under debate is an open
  question in `project-prd.md`, not a decision.
- **Fixed, ordered fields**: Date · Decision · Why · Rejected(×N). `Why` is mandatory — a decision
  with no why is a `spec.md` Stack line, not a decision. Include ≥1 `Rejected` unless there truly was
  no alternative (`- **Rejected**: none — no viable alternative`).
- **One decision per entry.** No "and also". Reference others inline (`supersedes D-030`).

## When to write vs leave it out
Write an entry when the choice **(a)** is expensive to reverse, **(b)** has a real alternative a
reasonable person would pick, or **(c)** will make a future reader ask "why this way?".

Do **not** write an entry for: a turn-level edit, a phrasing or style choice, anything obvious from
the code, or anything trivially re-derivable. **Curation happens at write time — an unfiltered ledger
becomes noise, and a noisy ledger stops being read.** Under-recording is recoverable (ask and append);
over-recording is not (nobody prunes a ledger).

## Reversing a decision (never edit or delete a past entry's body)
1. Append a **new** entry `D-071` for the new choice, naming `supersedes D-042` in its **Why**.
2. Edit **only** the old entry's status line → `### D-042 · … · superseded by D-071`. Its body stays
   100% intact — the original reasoning is the trail that explains how you got here.
3. Update **both** sides (forward `superseded by` + back-reference `supersedes`). Updating one side
   only is the #1 rot: a reversed decision with no forward pointer makes a reader act on a dead
   choice.

## Don't
- Restate a decision as a `spec.md` Stack line as well — state it once here and reference `D-NNN`
  from spec. Two homes for one fact is a drift guarantee.
- Record a decision nobody made yet (that's an open question).
- Renumber, reorder, or compact the file. Append-only means append-only.
