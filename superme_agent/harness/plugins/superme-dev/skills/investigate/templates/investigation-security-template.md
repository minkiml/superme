# Security review — {title}

## Questions
<fill:WRITTEN FIRST, before any code. The questions this sweep answers, as questions; then the WALLS, what is in scope and what is explicitly not, naming the area or saying whole-repo; then what DONE means. Update each question's state as you go — answered | partial | open>

## Attack surface
<fill:OPEN WITH THE BREADTH — "whole repo, by boundary class" or the area named, and what this codebase handles that would be costly to lose. Then two numbers: the ENUMERATED boundaries — routes, tool arguments, shell calls, deserialization, file paths built from input, stored data later trusted — with its size, and how many you WALKED end to end. Say which you only listed>

## Exposures
<fill:each one as a PATH, end to end: what an attacker controls, how it reaches the vulnerable code, what they get. Plus where (file.py:214) and severity. A worry with no path is not a finding — record it as a question instead>

## What is defended
<fill:what you probed that held, and what defends it, at file:line. "No exposures found" is unreadable without this section>

## Follow-up work
<fill:what should become work-items, by urgency — anything with a live path to harm first. Say which are fixes and which are hardening; they are not the same priority>

## Open threads
<fill:the unsampled remainder with its size, anything you could not reach (a dependency's internals, a runtime you could not exercise), and anything outside the Boundaries — or "none">
