# Auditing a surface of this codebase

Read before an audit: the item names a surface and asks whether it is sound — test coverage,
performance, the logic of a subsystem, whether a feature does what it promises, what is broken.

Security and dead code are their OWN families (`security.md`, `housekeeping.md`). If the item is
really asking one of those questions, say so in your report rather than doing half of it here.

## Contents

- **Two breadths, one bar** — what changes when the subject is the whole repo
- **The surface first** — why an audit starts with a list, not a file
- **Sampling** — what to read closely once the list is too long to read closely
- **Severity** — the word that has to mean something
- **Fan-out** — splitting without losing the thread
- **Where to start** — by what is being audited
- **The follow-up is half the job**
- **Three things to do instead** — the moves at the three points an audit goes wrong

## Two breadths, one bar

A standing sweep arrives one of two ways, and the item says which: **the whole repo**, or **one area
it names**. The bar does not move — a finding is a finding at either breadth — but the ENUMERATION
does, and so does what your numbers mean.

| breadth | how you enumerate | what "it held" then claims |
|---|---|---|
| **whole repo** | by CATEGORY, across the tree: every route handler, every command, every state machine. You will not read them all, and the category list is exactly what makes a sample honest | the categories held at the depth you sampled — no more |
| **one area** | EXHAUSTIVELY, inside the named area: every place in it where the property could fail | the area held, and that claim is much stronger |

**Open `## Surface & sample` with the breadth, in those words** — "whole repo" or the area named.
Every number after it is read against that one fact: "12 of 41" says nothing until the reader knows
whether 41 was the repository or one module.

## The surface first

An audit's failure mode is anecdote: three interesting findings from wherever you happened to look,
presented as the state of the system. So the first artifact is a LIST — every place the property
could fail, enumerated before any of them is read closely.

- Coverage → every behaviour the subject promises, then which have a test.
- Performance → every operation on the hot path the item names, with its input size.
- Logic → every branch and every state the subject can be in, including the ones nobody expects.
- A feature or service → everything it claims, from its own docs and its callers' assumptions.

Record it in `## Surface & sample` with its size. `"41 route handlers, 12 taking a path argument"`
is what makes every later number readable — including the ones you did not get to.

## Sampling

When the list is longer than the run, sample deliberately and say how:

- **Cover every category once** before covering any category twice.
- **Weight by blast radius**, not by convenience: the handler nobody calls is not where an audit
  spends its second hour.
- **State the sample.** "12 of 41, chosen as every handler taking a path argument" is a finding a
  reader can act on. "A number of handlers were reviewed" is not.

An unsampled remainder is not a gap you hide — it is `## Open threads`, with its size, so the next
pass starts where this one stopped.

## Severity

Severity is what the owner routes on, so it has to be defensible:

- **high** — this should not stay in main. There is a concrete impact, and you can name it.
- **medium** — real, but it needs a condition that isn't guaranteed. Say which.
- **low** — true, worth knowing, nobody's day changes.

Every finding names WHERE (`file.py:214`) and what actually goes wrong for a user, a caller or a
load pattern. A finding you cannot state as an impact is a suspicion — record it as one, in those
words.

**"Nothing found" is a real result**, and the one an audit most needs to state well: the enumerated
surface, what you sampled, and that it held. An audit that returns findings it had to reach for is
worse than one that returns none.

## Fan-out

Audits parallelize by AREA: one subagent per subsystem or per category from the list, each returning
findings with `file:line` and the reasoning that got there — never a verdict on its own.

Two jobs do not delegate: **cross-checking** a finding against the code yourself before it enters the
record, and **severity**, which needs the whole picture a single subagent never has.

**In the brief:** the three severity definitions above, pasted, and the rule that a finding names
WHERE and what goes wrong for a user, a caller or a load pattern. A subagent given no severity bar
comes back with "important" and "critical", and you re-derive every one of them by hand.

## Where to start

| auditing | start at | the recurring miss |
|---|---|---|
| test coverage | the behaviours promised, not the lines executed | a suite that is green because it asserts what the code does, not what it owes |
| performance | the operation the item names, at a realistic input size — then measure it | an O(n²) that is fine at n=10 and ships because nobody tried n=10,000 |
| logic | the states nobody designed for — empty, concurrent, partial, retried | reading the happy path and calling the branch coverage done |
| a feature or service | what it promises, then what its callers actually assume | auditing the implementation against itself instead of against the promise |
| bugs | the reports and the failure modes already known, before hunting new ones | hunting novel bugs while a known one stays unexplained |

## The follow-up is half the job

An audit that ends in a list of findings has done half the work. `## Follow-up work` turns them into
things someone can file — most severe first, one line each on what it would fix and roughly what it
touches. Group findings that share a fix into one item; a reader should not have to re-derive that
five findings are the same mistake in five places.

Nothing is filed from here. The owner files at the review gate, and this section is what makes that
one decision instead of a re-reading.

## Three things to do instead

Each of these is where an audit most often goes wrong, written as the move that keeps it right.

- **Found something broken? Write it down and keep auditing.** The fix belongs in
  `## Follow-up work`, sized so the owner can file it — that is what makes it a decision they made
  rather than a change that appeared. A research item is read-only on real code, so the discipline
  and the contract agree here.
- **Disagreeing with a recorded decision? Audit it AS a decision.** Name the decision from the anchor
  docs, say what it costs in what you just measured, and put it to the owner as a finding about the
  decision. That is a real and valuable result; measuring against a stricter bar than the project
  actually set, without saying so, is a finding nobody can act on.
- **Order by severity, always.** The most severe finding goes first even when the subtle one was more
  satisfying to find. Severity is what the owner routes on, and it is the only order the report has.
