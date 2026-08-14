# Studying something built outside this repo for improving this codebase

Read before a study. The item names something (a codebase(s), web resources, so on) outside this repo — a project that solved the problem
we have, a library we might depend on, a product whose design we want, a published method — and asks
what we should take from it.

## Contents

- **Pin the source** — what keeps a claim checkable later
- **Two bars** — what they do vs what we should take
- **The transfer test** — what promotes an observation to a proposal
- **Copying** — a licence question, not yours to settle
- **Fan-out** — split by source, keep the comparison
- **Where to start** — by what you are studying
- **The follow-up: what adopting it would take**
- **What a study does NOT do**

## Pin the source

The first thing in `## Source` is which snapshot you read: a repo's commit, a library's exact version
*and* the version we would resolve to, a page's URL and the date.

Then keep two kinds of source apart. What they SAY they do — README, design doc, talk — is evidence
about intent. The code and its behaviour are evidence about the thing. When the two disagree, the
code wins, and the disagreement is usually the most interesting line in the study.

## Two bars

- **What they do** is evidence: anyone reading the same snapshot can check it.
- **What we should take** is judgment: the owner decides on it.

The template keeps these in separate sections, because one sentence carrying both is how something
gets adopted without anyone choosing it. "They cache the parsed tree, so we should too" is two
claims, and only the first has a receipt.

## The transfer test

An observation becomes a proposal once you have said what could void it:

- **Scale** — at their input sizes, not ours. What pays for itself at their volume can be pure
  overhead here, and the reverse.
- **Constraints** — what they must satisfy that we don't (a public API they can't break,
  multi-tenancy, a platform target), and what we must satisfy that they don't.
- **Cost to run it** — a pattern a large team maintains is not free here because its code is small.
- **What it lands on HERE** — our file, our subsystem, named. Unnamed, the proposal is untestable,
  and this is the single most common thing a study gets wrong.

Survives none of them? It stays in `## What they do`, and saying so is a result.

## Copying

Reading is free. A block of their source in our tree is a licence question and not yours to settle:
it goes in the report as a proposal with the licence named, never as a quiet paste. Describe the
mechanism in your own words and there is nothing to settle.

## Fan-out

One subagent per source, or per subsystem inside a large one. Each returns what that source does with
a pointer — never whether we should take it. The comparison stays yours: a subagent that has read
only them cannot know what transfers, because it has not read us.

**In the brief:** what pins a source (a commit, an exact version, a URL and the date read) and the
split between what they SAY they do and what their code does. Ask for that split by name — it is
where the interesting line usually is, and a subagent reading a README will otherwise hand you the
intent and call it the thing.

## Where to start

| studying | start at | the recurring miss |
|---|---|---|
| a codebase | its shape — entry points and module boundaries — before any single file | mistaking the newest layer for the design; check whether you are reading the architecture or the third rewrite of one corner |
| a library we might depend on | its tests and its issue tracker | judging it by the README's happy path — the failure modes we would inherit live in the issues |
| a product's design | the path a user actually walks, end to end | copying the surface without the constraint that produced it |
| a paper, spec or dataset | the claim, and the conditions it holds under | quoting a result whose conditions we cannot reproduce as if it were ours |

## The follow-up: what adopting it would take

A study that ends at "they do this and it's good" has stopped one step short of being useful.
`## Follow-up work` says what taking it would actually cost here: the items, in landing order, each
naming what it touches in OUR code.

Sized honestly. "Adopt their caching approach" is not an item; "add a parse cache keyed on file
mtime in `dev_knowledge.read_work_item`, ~1 file" is. If the honest answer is that adopting it is a
month, say a month — an underestimate here is how a study gets something started that nobody would
have started knowingly.

## What a study does NOT do

- **It does not adopt.** Nothing changes in our code because a study liked it — that is the owner's
  call at the review gate.
- **It does not file work.** An idea is an open thread, then a proposal in the report.
- **It does not audit them.** Their bugs are theirs, unless we would inherit one — then name it and
  say what we would inherit.
- **It does not bring the material home.** A clone or a bulk dataset has no place in the item folder;
  raise it as a wall.
