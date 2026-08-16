# Refactoring study — {title}

## Questions
<fill:WRITTEN FIRST, before any code. The questions this sweep answers, as questions; then the WALLS, what is in scope and what is explicitly not, naming the area or saying whole-repo; then what DONE means. Update each question's state as you go — answered | partial | open>

## What makes it hard
<fill:OPEN WITH THE BREADTH AND WHERE THE CANDIDATES CAME FROM — "whole repo, hot spots from N commits" (the git history is the whole-repo enumeration, not the file tree) or the area named. Then the evidence, in the code, BEFORE any proposal — the duplication, the coupling, the function nobody can change safely, with file:line. Where you can, the cost in something observable: how many places a change has to touch, how often this area breaks. "It feels messy" is not evidence>

## Proposed shape
<fill:what it should look like instead, concretely enough to argue with — the boundaries, what moves where, what stops existing, what stays. Name the files. Give the alternative you rejected and why. For anything you propose removing, run the deletion test and say which happened: the complexity vanished, or it reappeared at the callers>

## What the move costs
<fill:blast radius (which files, which callers, which tests — counted), what breaks on the way, the order it must land in, what is unsafe to leave half-done, and what the code cannot tell you>

## Follow-up work
<fill:the implementation items this implies, in landing order, each small enough that main is consistent and the tests pass once it lands>

## Open threads
<fill:what you could not settle, alternatives you considered and dropped with why, and anything outside the Boundaries — or "none">
