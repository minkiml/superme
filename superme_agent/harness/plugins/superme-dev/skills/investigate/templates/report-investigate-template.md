# Investigation User-facing Report

**Summary:** <fill:one line — the answer, or how far it got. This line is what the dashboard shows while the item is investigating, so it has to stand alone>

## At a glance
<fill:ONE TABLE, one row per finding, in the order they should be acted on. Columns: a short id (A, B, C…) · what it is, in plain words · its size (how many files or symbols) · your confidence and what it rests on · what to do about it. This table is what the card and the drilldown show first, so a reader who stops here should still know what was found and what happens next>

## Findings
<fill:ONE BLOCK PER ROW ABOVE, same ids, same order, each complete on its own so nobody has to scroll to act on it. Per block: `### A · <name>`, then **What** — plain sentences carrying NO file paths or symbol names; then **Where** — a small table or fenced list holding the paths and symbols the sentences referred to; then **Proof** — how it was established, and what would have falsified it; then **Do** — the action and how safe it is. Keep identifiers out of the prose and inside the table: a sentence with three backticked names in it is unreadable at a glance>

## Coverage
<fill:A TABLE, not a paragraph. One row per kind or area swept. Columns: what was enumerated and its size · what was checked, at what depth · what was not reached. Where a number is provable say so; where it is a sample, say how the sample was chosen. Add one line below the table for any tool that was unavailable and what that leaves unverified>

## Open questions
<fill:the questions this sweep raised and could not answer, and what would answer each one. Only what belongs to no single finding — anything specific to one finding lives in that finding's block. These are a result, not a shortfall: a sweep that surfaces a question nobody had asked has done its job>
