# Security review — {title}

## Questions
<fill:the plan's questions, each with where it now stands — answered | partial | open>

## Attack surface
<fill:every place untrusted input enters or crosses a boundary — routes, tool arguments, shell calls, deserialization, file paths built from input, stored data that is later trusted. Enumerated with its size, before any of it is read closely>

## Exposures
<fill:each one as a PATH, end to end: what an attacker controls, how it reaches the vulnerable code, what they get. Plus where (file.py:214) and severity. A worry with no path is not a finding — record it as a question instead>

## What is defended
<fill:what you probed that held, and what defends it. This matters more here than in any other family: "no exposures found" is unreadable without it, and the defence you name is what a later change must not quietly remove>

## Follow-up work
<fill:what should become work-items, by urgency — anything with a live path to harm first. Say which are fixes and which are hardening; they are not the same priority>

## Open threads
<fill:the unsampled remainder with its size, anything you could not reach (a dependency's internals, a runtime you could not exercise), and anything outside the Boundaries — or "none">
