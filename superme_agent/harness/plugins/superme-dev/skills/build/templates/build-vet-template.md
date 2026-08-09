# Build⟷vet {cycle} — {title}

## Built
<fill:one bullet per task, each LEADING with its task id — `- t1 — what was implemented and the files touched · how to exercise it · errors, gaps, and concerns found · assumptions made · authorization requests (ids only)`. A bullet that belongs to no single task (a shared refactor, a stale doc fixed in passing) leads with no id and reads as item-wide.>

## For the reviewer
<!-- Read on the PR page, beside each task's own commits — the ONLY thing there a machine cannot
     derive from the plan, the trailers and the ledger. One line per task you touched this cycle,
     exactly this shape, and the LATEST cycle's line wins:
       - t1 — look: <where a reader's attention pays, and why> · deviated: <what plan.md said → what
         you built instead, and why, or `none`>
     `look` is not a summary of the change — the diff is right there. It is the thing the diff does
     not show: the case you chose not to handle, the value nobody specified, the call that could
     have gone the other way inside this task. Nothing worth pointing at ⇒ write `look: none`, and
     say `deviated: none` when you built what the plan said. Both empty on every task is a normal
     cycle, not a failure to fill this in. -->
<fill:one line per task in the shape above>

## Validation
<!-- the ```runs fence below is appended by `record_validation` — never hand-edit it. The
     bullets are yours: the per-task narrative a vetter reads. -->
<fill:the internal checks run — unit / compile / mock / synthetic — results verbatim. Per-task lines LEAD with the task id (`- t1 — 12 unit tests pass`); a whole-item run (the suite, a lint pass) leads with no id.>

## Verification
<!-- appended by vet's recording tool — a fenced check table; never hand-edit -->

## Cycle outcome
<!-- appended by the loop driver — decision + reason; never hand-edit -->
