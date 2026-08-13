# Build⟷vet {cycle} — {title}

## Built
<fill:one bullet per task, each LEADING with its task id — `- t1 — what was implemented and the files touched · how to exercise it · errors, gaps, and concerns found · assumptions made · authorization requests (ids only)`. A bullet that belongs to no single task (a shared refactor, a stale doc fixed in passing) leads with no id and reads as item-wide.>

## For the reviewer
<fill:one line per task you touched this cycle, exactly this shape — `- t1 — look: <where a reader's attention pays, and why> · deviated: <what plan.md said → what you built instead, and why, or `none`>`. The LATEST cycle's line wins. `look: none` on every task is a normal cycle.>

## Validation
<fill:the internal checks run — unit / compile / mock / synthetic — results verbatim. Per-task lines LEAD with the task id (`- t1 — 12 unit tests pass`); a whole-item run (the suite, a lint pass) leads with no id. The ```runs fence below this is appended by `record_validation` and is machine-owned — never hand-edit it.>

## Verification

## Cycle outcome
