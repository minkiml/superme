# Refactoring study — {title}

## Questions
<fill:WRITTEN FIRST, before any code. The questions this sweep answers, as questions — yours to set from the brief (or, for a button-launched sweep, from the item's own title and description); then the WALLS, what is in scope and what is explicitly not, naming the area or saying whole-repo; then what DONE means. Update each question's state as you go — answered | partial | open. A reader who has not seen the brief should be able to tell from this section alone what was and was not looked at>

## What makes it hard
<fill:OPEN WITH THE BREADTH AND WHERE THE CANDIDATES CAME FROM — "whole repo, hot spots from N commits" (the git history is the whole-repo enumeration, not the file tree) or the area named. Then the evidence, in the code, BEFORE any proposal — the duplication, the coupling, the function nobody can change safely, with file:line. Where you can, the cost in something observable: how many places a change has to touch, how often this area breaks. "It feels messy" is not evidence>

## Proposed shape
<fill:what it should look like instead, concretely enough to argue with — the boundaries, what moves where, what stops existing. A shape nobody could disagree with is too vague to be useful>

## What the move costs
<fill:blast radius (which files, which callers, which tests), what breaks on the way, the order it would have to land in, and what makes it risky. An honest cost is what stops this being adopted on optimism>

## Follow-up work
<fill:the implementation items this implies, in landing order, each one small enough to stand alone. This item does not change code — the change is separate work, and this section is what makes it filable>

## Open threads
<fill:what you could not settle, alternatives you considered and dropped with why, and anything outside the Boundaries — or "none">
