# Authoring contract — `general/decisions.md`

**What it is.** The **append-only ledger** of standing rules: what now holds, why, and what settled
it. The durable answer to *"wait, why is it done this way?"* — asked by a future agent or a future
you, neither of whom has heard of the work item the rule came out of.

**What it is not.** A log of instructions given. "Delete this file" is spent the moment the file is
gone, and a reader who meets it later learns nothing. An entry earns its place by binding work
nobody has proposed yet.
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
### D-042 · <the rule, verbatim> · accepted
- **Date**: <YYYY-MM-DD>
- **Rule**: <what now holds — one declarative present-tense sentence that stands on its own>
- **Why**: <the forcing context + rationale — 1–3 sentences>
- **Rejected**: <alternative> — <the one reason it lost>
```

The field is `Rule`, not `Decision`. A field named for the act invites the act to be recorded ("we
decided to delete it"); a field named for the result only accepts a result. Entries written before
this rename keep their `Decision` line — bodies are never edited — and read the same, because the
declarative-sentence demand was always the point.

**The heading is the rule, verbatim.** Every phase reads the headings and nothing else before asking
the owner anything, so a heading that names the ticket instead of the rule costs a reader the body.

- **ID** `D-NNN`, zero-padded, monotonic, **never reused** — it's the grep anchor and the
  supersession target.
- **Status is inline in the heading** so scanning `### D-` lines shows state without opening bodies.
  Three values only:
  - `accepted` — in force.
  - `superseded by D-NNN` — replaced by a later decision.
  - `deprecated` — no longer relevant (the thing it governed is gone), but kept for history.

  No `proposed` — this ledger records **settled** choices. A choice still under debate is an open
  question in `project-prd.md`, not a decision.
- **Fixed, ordered fields**: Date · Rule · Why · Rejected(×N). `Why` is mandatory — a rule with no
  why is a `spec.md` Stack line, not a decision. Include ≥1 `Rejected` when you know what lost.
  **Omit the field rather than invent one.** A `Rejected` line that says "the other option" tells a
  reader nothing and makes the entry look better-sourced than it is; entries appended by the kernel
  from an owner's ruling carry no `Rejected` for exactly this reason, and carry instead a
  `- **Ruling that settled it**:` line and a `- **Source**:` line naming the item and the question.
- **One rule per entry.** No "and also". Reference others inline (`supersedes D-030`).

## When to write vs leave it out
Write an entry when the rule passes BOTH tests:
- **Standalone** — someone who never heard of the work it came from can act on it. Name no file that
  work touched, no item id, nothing the reader must go and look up.
- **Load-bearing** — a real alternative exists that a reasonable person would pick, and a future
  reader would otherwise ask "why this way?".

Do **not** write an entry for: an instruction that is spent when its work is done, a turn-level edit,
a phrasing or style choice, anything obvious from the code, or anything trivially re-derivable.
Nor for a truism ("prefer deleting dead code") — if no future call goes differently, it settles
nothing. **Curation happens at write time — an unfiltered ledger becomes noise, and a noisy ledger
stops being read.** Under-recording is recoverable (ask and append); over-recording is not: nobody
prunes a ledger, and every phase reads it before asking anything, so an over-broad entry silently
suppresses questions that should have been asked.

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
- Record a rule nobody has settled yet (that's an open question).
- Restate a rule that a `D-NNN` already carries. Read the headings first; a second entry saying the
  same thing in other words leaves a reader unable to tell which one is in force.
- Renumber, reorder, or compact the file. Append-only means append-only.
