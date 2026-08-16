# Refactoring study — what shape should this be

The item names code that is hard to work in and asks what shape it should have instead. You produce
a shape, the case for it, and the honest cost. The change itself is separate implementation work the
owner files from your follow-up.

## Contents

- **The bar** — what counts as evidence here
- **Pick your breadth** — the git history is the whole-repo enumeration
- **Measure the difficulty**
- **Draw the proposed shape**
- **Price the move**
- **Splitting the work**
- **Shaping the follow-up** — it IS the proposal
- **What a refactoring study does NOT do**

## The bar

Evidence is the code, at `file:line` — the duplication, the coupling, the function whose callers all
pass the same three arguments, the module that everything imports. A reader who disagrees with your
proposal should still agree with your evidence.

**"It feels messy" is not evidence.** Neither is "this is not how it is usually done".

Write `## What makes it hard` before `## Proposed shape`, in that order. The failure mode of this
family is a shape you liked, justified afterwards.

## Pick your breadth

The item says which. The bar is the same; where you look for candidates is not.

| breadth | what you enumerate |
|---|---|
| **whole repo** | **the GIT HISTORY, not the file tree.** Walk `git log` over a good stretch and find what keeps coming back — the files that appear in change after change, the areas where fixes cluster. Deepening pays off in FUTURE changes, so the code changed most is where the payoff is; a module nobody has touched in a year is not hard to work in, it is finished. A tree walk finds big files, which is a different question |
| **one area** | the named module **and everything that calls it**. The shape question is about a boundary, and a boundary cannot be judged from one side |

Open `## What makes it hard` with the breadth and how you found your candidates — "whole repo, hot
spots from 400 commits", or the area named.

## Measure the difficulty

Put a number on it where you can; the number is what survives disagreement.

- **How many places one change touches.** Trace a real recent change, or a plausible one the roadmap
  implies, and count the files it has to visit.
- **How often this area breaks.** Count the fixes that have landed here, and whether they cluster.
- **How much is duplicated.** Not "there is duplication" — how many copies, and whether they have
  drifted apart. Drifted copies are the stronger argument.
- **What cannot be tested as it stands**, and what it would take to make it testable.

A measurement you cannot make is fine when you say which one it was and what it would have settled.

## Draw the proposed shape

Concrete enough to argue with: the boundaries, what moves where, what stops existing, what the new
seams are. Name the files.

- **Show the shape, not the diff.** You are describing the destination well enough that someone else
  can plan the route.
- **Say what stays.** A proposal that touches everything is a rewrite wearing a refactor's name, and
  the owner should be told that plainly if that is what it is.
- **Give the alternative you rejected**, and why.

**Run the deletion test on anything you propose to remove.** Imagine the module gone, and say which
happens: the complexity VANISHES (it was a pass-through, and deleting it is the whole proposal), or
it REAPPEARS across its callers (it was earning its keep, and the proposal has to say where that
complexity goes instead). Moved complexity comes back at the call sites, where it is harder to see.

## Price the move

`## What the move costs` is the section the decision turns on.

- **Blast radius** — which files, which callers, which tests. Count them.
- **What breaks on the way** — anything that cannot move without a temporary inconsistency, and any
  interface someone outside this repo depends on.
- **The order it must land in**, and what is unsafe to leave half-done.
- **What the code cannot tell you** — behaviour that only exists in production, config that varies
  per environment.

## Splitting the work

Split by AREA when the subject is large — one reader per module, each returning what makes THAT part
hard, at `file:line`.

Paste into every brief what counts as evidence here, and the line that "it feels messy" is not it.
Ask for what makes that part hard, and say plainly that the shape is yours to draw, not theirs.

**The shape does not delegate.** It is a whole-system judgment, and a reader proposing one for its
own corner is how a refactor becomes six incompatible refactors.

## Shaping the follow-up

For this family `## Follow-up work` is not a postscript — it is the thing the owner acts on. Write it
as a landing sequence:

- **Each item stands alone.** After it lands, main is consistent and the tests pass. If a step only
  makes sense with the next one, it is not a step.
- **In order**, with what blocks what.
- **Smallest first where you can**, so the sequence can stop partway and still leave the code better
  than it was. A migration that only pays off at the end is a bet, and should be labelled one.

## What a refactoring study does NOT do

- **It does not refactor.** Not even the trivial part, not "to demonstrate".
- **It does not add behaviour.** A shape proposal that quietly includes a feature is two decisions in
  one, and the owner will only see one of them.
- **It does not audit.** Bugs you notice on the way are open threads; a bug hunt inside a refactoring
  study is a different item.
