# Audit — {title}

## Questions
<fill:WRITTEN FIRST, before any code. The questions this sweep answers, as questions; then the WALLS, what is in scope and what is explicitly not, naming the area or saying whole-repo; then what DONE means. Update each question's state as you go — answered | partial | open>

## Surface & sample
<fill:OPEN WITH THE BREADTH — "whole repo" or the area named. Then two numbers that mean different things: the ENUMERATED surface, everything the property could fail in, with its size ("41 route handlers, 12 taking a path argument"), and the SAMPLE, what you read closely and the rule you chose it by ("12 of 41, every handler taking a path argument"). Say plainly what you did not reach>

## Findings
<fill:each one — where (file.py:214), what is wrong, severity, and the concrete impact. A finding you cannot state as an impact is a suspicion; record it as one, in those words>

## What held
<fill:the parts of the surface you checked that were sound, and at what depth. "Nothing found" means nothing without this>

## Follow-up work
<fill:what should become work-items, most severe first — one line each on what it would fix and roughly what it touches. Group findings that share one fix into one item>

## Open threads
<fill:the unsampled remainder with its size, and anything outside the walls you set above (parked, never chased) — or "none">
