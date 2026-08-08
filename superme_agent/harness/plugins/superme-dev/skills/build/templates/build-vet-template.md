# Build⟷vet {cycle} — {title}

## Built
<fill:one bullet per task, each LEADING with its task id — `- t1 — what was implemented and the files touched · how to exercise it · errors, gaps, and concerns found · assumptions made · authorization requests (ids only)`. A bullet that belongs to no single task (a shared refactor, a stale doc fixed in passing) leads with no id and reads as item-wide.>

## Validation
<!-- the ```runs fence below is appended by `record_validation` — never hand-edit it. The
     bullets are yours: the per-task narrative a vetter reads. -->
<fill:the internal checks run — unit / compile / mock / synthetic — results verbatim. Per-task lines LEAD with the task id (`- t1 — 12 unit tests pass`); a whole-item run (the suite, a lint pass) leads with no id.>

## Verification
<!-- appended by vet's recording tool — a fenced check table; never hand-edit -->

## Cycle outcome
<!-- appended by the loop driver — decision + reason; never hand-edit -->
