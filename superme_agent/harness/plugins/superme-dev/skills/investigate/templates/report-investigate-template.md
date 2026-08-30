# Investigation User-facing Report

**Summary:** <fill:one line, under 25 words — the answer, or how far it got. The dashboard shows this line alone>

## At a glance
<fill:ONE TABLE, one row per finding, in the order to act on them. Columns: id (A, B, C…) · what it is, under 12 words · size · confidence and what it rests on · what to do. A reader who stops here still knows what was found and what happens next>

## Findings
<fill:ONE BLOCK PER ROW ABOVE — same ids, same order, each complete on its own. Per block `### A · <name>`, then four labels:
**What** — 2–4 bullets, each under 20 words, ONE FACT EACH. A bullet with a semicolon is two bullets. No file paths or symbol names; they go in Where.
**Where** — a table or fenced list of the paths and symbols, then a BLANK LINE (a label under the last row reads as one more row).
**Proof** — 2 bullets, each under 20 words. One per bullet: how it was established · what would have falsified it.
**Do** — 2 bullets, each under 20 words. One per bullet: the action · how safe it is>

## Coverage
<fill:A TABLE, not prose. One row per kind swept. Columns: what was enumerated and its size · what was checked and at what depth · what was not reached.
Name each row by the KIND your family's guide defines, never by the pass you ran — a row headed by a technique reads as coverage of the whole kind, and is not.
Write "complete" only when the arithmetic closes: enumerated minus reached equals what you reported.
Then one line: how many of your tool calls were refused (the kernel gives you that number — quote it) and what that left unverified. Zero is a real answer>

## Open questions
<fill:bullets, each under 20 words — a question this raised and could not answer, and what would answer it. Only what belongs to no single finding. These are a result, not a shortfall>
