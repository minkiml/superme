# Refactoring study — what shape should this be

Read before a refactoring investigation: the plan names code that is hard to work in and asks what
shape it should have instead.

**This item does not change the code.** It produces a shape, the case for it, and the honest cost;
the change is separate implementation work that the owner files from your follow-up. That boundary
is what makes the proposal worth trusting — nobody is arguing for a rewrite they have already
started.

## Contents

- **Evidence before proposal** — and why this order is the whole discipline
- **Making "hard to work in" measurable**
- **The proposed shape** — concrete enough to argue with
- **The cost** — what makes this honest
- **Fan-out**
- **The follow-up IS the proposal**
- **What a refactoring study does NOT do**

## Evidence before proposal

The failure mode here is aesthetic: a shape you like, justified afterwards. So the artifact puts
`## What makes it hard` before `## Proposed shape`, and you write them in that order.

Evidence means the code, at `file:line` — the duplication, the coupling, the function whose callers
all pass the same three arguments, the module that everything imports. A reader who disagrees with
your proposal should still agree with your evidence.

**"It feels messy" is not evidence.** Neither is "this is not how it is usually done".

## Making "hard to work in" measurable

Where you can, put a number on the difficulty, because the number is what survives disagreement:

- **How many places one change touches.** Trace a real recent change, or a plausible one the roadmap
  implies, and count the files it has to visit.
- **How often this area breaks.** The git history is evidence: how many fixes have landed here, and
  whether they cluster.
- **How much is duplicated.** Not "there is duplication" — how many copies, and whether they have
  already drifted apart (drifted copies are a stronger argument than identical ones).
- **What cannot be tested as it stands**, and what it would take to make it testable.

A measurement that cannot be made is fine when you say which one it was and what it would have
settled.

## The proposed shape

Concrete enough to argue with: the boundaries, what moves where, what stops existing, what the new
seams are. Name the files.

- **Show the shape, not the diff.** You are not writing the change; you are describing the
  destination well enough that someone else can plan the route.
- **Say what stays.** A proposal that touches everything is usually a rewrite wearing a refactor's
  name, and the owner should be told that plainly if that is what it is.
- **Give the alternative you rejected**, and why. A proposal with no discarded option reads as the
  first idea, and often is.

## The cost

`## What the move costs` is the section that decides whether this is adopted, and understating it is
the fastest way to lose the argument later:

- **Blast radius** — which files, which callers, which tests. Count them.
- **What breaks on the way** — anything that cannot be moved without a temporary inconsistency, and
  any interface someone outside this repo depends on.
- **The order it must land in**, and what is unsafe to leave half-done.
- **What the code cannot tell you** — behaviour that only exists in production, config that varies
  per environment.

## Fan-out

Split by AREA when the subject is large: one subagent per module, each returning what makes THAT
part hard, with `file:line` — never a proposed shape. Synthesis does not delegate. A shape is a
whole-system judgment, and a subagent proposing one for its own corner is how a refactor becomes six
incompatible refactors.

## The follow-up IS the proposal

For this family, `## Follow-up work` is not a postscript — it is the thing the owner acts on. Write
it as a landing sequence:

- **Each item stands alone.** After it lands, main is consistent and the tests pass. If a step only
  makes sense with the next one, it is not a step.
- **In order**, with what blocks what.
- **Smallest first where you can**, so the sequence can be stopped partway and still leave the code
  better than it was. A migration that only pays off at the end is a bet, and it should be labelled
  one.

## What a refactoring study does NOT do

- **It does not refactor.** Not even the trivial part, not "to demonstrate".
- **It does not add behaviour.** A shape proposal that quietly includes a feature is two decisions in
  one, and the owner will only see one of them.
- **It does not audit.** Bugs you notice on the way are worth recording as open threads, but the
  question here is shape, and a bug hunt inside a refactoring study is a different item.
