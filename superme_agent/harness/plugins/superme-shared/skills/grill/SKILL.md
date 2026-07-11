---
name: grill
description: Interview the user relentlessly about what user is asking for until reaching shared understanding, resolving each branch of the decision tree. Use when user mentions "grill me".
---

Interview me relentlessly about every aspect of what user is asking for until we reach a shared understanding. Walk down each branch of the design tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer. 

Ask the questions one at a time.

If a question can be answered by exploring the codebase, explore the codebase instead.

Once grilling is complete, summarize the shared understanding in a clean and concise words into a .md file under ./general_docs/.


<prd-template>

## Problem Statement

The problem that the user is facing, from the user's perspective.

## High-level Solution(s) OR Vision Statement

The solution to the problem, from the user's perspective.

See the big picture rather than the small details.

## User Stories

A LONG, numbered list of user stories. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit>

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending
</user-story-example>

This list of user stories should be extremely extensive and cover all aspects of the feature.

## Key Decisions made

A list of implementation decisions that were made. This may include:

- The logics and/or modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it within the relevant decision and note briefly that it came from a prototype. Trim to the decision-rich parts — not a working demo, just the important bits.

## TODOs for Certain

A possible list of TODOs that are certainly required and good to begin with straight in the next **implementation job** in order to make things (e.g., what user really wants, ) more clearer, and what what can follow after it. More specifically, this can be a list of possible foundational implementations such as designing and creating a (initial) interface to begin with; (then, for "what can follow:" with this, you can then ask user about what they want in the interface, and then you can iterate on the design of the interface based on their feedback).

Try to see the big picture rather than the small details.

## Out of Scope

A description of the things that are out of scope for this PRD.

## Further Notes

Any further notes about the feature.

</prd-template>