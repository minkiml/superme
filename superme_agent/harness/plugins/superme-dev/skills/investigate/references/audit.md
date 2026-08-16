# Auditing a surface of this codebase

The item names a surface and asks whether it is sound — test coverage, performance, the logic of a
subsystem, whether a feature does what it promises, what is broken.

Security and dead code are their own families (`security.md`, `housekeeping.md`). If the item is
really asking one of those, say so in your report rather than doing half of it here.

## Contents

- **The bar** — what counts as a finding here
- **Pick your breadth**
- **Enumerate the surface first**
- **Sample deliberately**
- **Severity**
- **Where to start**, by what is being audited
- **Splitting the work**
- **Shaping the follow-up**
- **Three things to do instead**

## The bar

A finding names WHERE (`file.py:214`) and what actually goes wrong — for a user, a caller, or a load
pattern. Something you cannot state as an impact is a suspicion; record it as one, in those words.

**"Nothing found" is a real result** when it comes with the enumerated surface and what you sampled.
An audit that returns findings it had to reach for is worse than one that returns none.

## Pick your breadth

The item says which: the whole repo, or one area it names. The bar does not move; the enumeration
does, and so does what your numbers mean.

| breadth | how you enumerate | what "it held" then claims |
|---|---|---|
| **whole repo** | by CATEGORY across the tree: every route handler, every command, every state machine. You will not read them all, and the category list is what makes a sample honest | the categories held at the depth you sampled — no more |
| **one area** | EXHAUSTIVELY inside the named area: every place in it where the property could fail | the area held, which is a much stronger claim |

State the breadth in the first line of `## Surface & sample`. Every number after it is read against
that one fact: "12 of 41" says nothing until the reader knows whether 41 was the repository or one
module.

## Enumerate the surface first

An audit's failure mode is anecdote — three interesting findings from wherever you happened to look,
presented as the state of the system. So the first thing you produce is a LIST: every place the
property could fail, enumerated before any of them is read closely.

- Coverage → every behaviour the subject promises, then which have a test.
- Performance → every operation on the hot path the item names, with its input size.
- Logic → every branch and every state the subject can be in, including the ones nobody expects.
- A feature or service → everything it claims, from its own docs and its callers' assumptions.

Record the list with its size. `"41 route handlers, 12 taking a path argument"` is what makes every
later number readable — including the ones you did not get to.

## Sample deliberately

When the list is longer than the run:

- **Cover every category once** before covering any category twice.
- **Weight by blast radius**, not by convenience. The handler nobody calls is not where an audit
  spends its second hour.
- **State the sample.**

**Bad and good examples**
```example
✗ "A number of handlers were reviewed."
✓ "12 of 41, chosen as every handler taking a path argument."
```

An unsampled remainder goes in `## Open threads` with its size, so the next pass starts where this
one stopped.

## Severity

Severity is what the owner routes on, so it has to be defensible:

- **high** — this should not stay in main. There is a concrete impact and you can name it.
- **medium** — real, but it needs a condition that isn't guaranteed. Say which.
- **low** — true, worth knowing, nobody's day changes.

## Where to start

| auditing | start at | the recurring miss |
|---|---|---|
| test coverage | the behaviours promised, not the lines executed | a suite that is green because it asserts what the code does, not what it owes |
| performance | the operation the item names, at a realistic input size — then measure it | an O(n²) that is fine at n=10 and ships because nobody tried n=10,000 |
| logic | the states nobody designed for — empty, concurrent, partial, retried | reading the happy path and calling the branch coverage done |
| a feature or service | what it promises, then what its callers actually assume | auditing the implementation against itself instead of against the promise |
| bugs | the reports and failure modes already known, before hunting new ones | hunting novel bugs while a known one stays unexplained |

## Splitting the work

Split by AREA — one reader per subsystem, or per category from your list.

Paste the three severity definitions into every brief, plus the rule that a finding names WHERE and
what goes wrong. A reader given no severity bar comes back with "important" and "critical", and you
re-derive every one of them by hand.

Two jobs do not delegate: **cross-checking** a finding against the code yourself before it enters the
record, and **severity**, which needs a whole picture no single reader has.

## Shaping the follow-up

Most severe first, one line each on what it would fix and roughly what it touches. Group findings
that share a fix into one item — a reader should not have to work out for themselves that five
findings are the same mistake in five places.

## Three things to do instead

- **Found something broken? Write it down and keep auditing.** The fix belongs in
  `## Follow-up work`, sized so the owner can file it — that is what makes it a decision they made
  rather than a change that appeared.
- **Disagreeing with a recorded decision? Audit it AS a decision.** Name the decision from the anchor
  docs, say what it costs in what you just measured, and put it to the owner as a finding about the
  decision. Measuring against a stricter bar than the project set, without saying so, is a finding
  nobody can act on.
- **Order by severity, always.** The most severe finding goes first even when the subtle one was more
  satisfying to find. It is the only order the report has.
