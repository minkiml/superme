# Auditing a surface of this codebase

Read before an audit: the plan names a surface and asks whether it is sound — test coverage,
performance, the logic of a subsystem, whether a feature does what it promises, what is broken.

Security and dead code are their OWN families (`security.md`, `housekeeping.md`). If the plan is
really asking one of those questions, say so in your report rather than doing half of it here.

## Contents

- **The surface first** — why an audit starts with a list, not a file
- **Sampling** — what to read closely once the list is too long to read closely
- **Severity** — the word that has to mean something
- **Fan-out** — splitting without losing the thread
- **Where to start** — by what is being audited
- **The follow-up is half the job**
- **What an audit does NOT do**

## The surface first

An audit's failure mode is anecdote: three interesting findings from wherever you happened to look,
presented as the state of the system. So the first artifact is a LIST — every place the property
could fail, enumerated before any of them is read closely.

- Coverage → every behaviour the subject promises, then which have a test.
- Performance → every operation on the hot path the plan names, with its input size.
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

## Where to start

| auditing | start at | the recurring miss |
|---|---|---|
| test coverage | the behaviours promised, not the lines executed | a suite that is green because it asserts what the code does, not what it owes |
| performance | the operation the plan names, at a realistic input size — then measure it | an O(n²) that is fine at n=10 and ships because nobody tried n=10,000 |
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

## What an audit does NOT do

- **It does not fix.** A fix inside an audit is an unreviewed change nobody planned.
- **It does not re-litigate a decision** already recorded in the anchor docs. A finding that a
  written decision is wrong is a finding ABOUT the decision — say that, don't quietly audit against
  a bar nobody set.
- **It does not rank by how interesting a finding is.** Severity is the only order.
