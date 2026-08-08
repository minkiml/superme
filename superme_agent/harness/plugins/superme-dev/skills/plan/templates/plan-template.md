# Plan — {title}

## Intent
<fill:1-3 lines — the outcome that answers brief.md's `## Problem`>

## Design
<fill:the approach and why this way over the alternatives · modules/files touched · interfaces and data shapes (signatures, schemas, routes) · constraints and gotchas discovered in directed reads · explicitly out of scope. Build implements this section verbatim and may not amend it>

## Decisions & clarifications
<!-- owner Q&A conclusions land here, append-only — starts empty. One entry per answered question:
### <ts> — <the question, one line>
- answer: <the owner's answer, in substance>
- changed: <what it changed in the plan, or "nothing">
-->

<!-- A revision appends `## Revision log` + a `## Revision r<n>` block HERE, above the two live
     sections below — everything above is the record, everything below is the current truth. Code
     writes those; never hand-author them. -->

## Tasks
<!-- Each task is a NAME on the head line and its SPECIFICATION on the indented lines under it. The
     head line is what the owner's board shows, so it is a few words naming the change — not the
     spec's first clause. The spec is what build reads; put every file, flag and rule there. -->
- [ ] t1 — <fill:the task's NAME — a few words, under ~60 chars, no closing period>
      <fill:the specification — files, functions, flags, rules, edge cases. Wrap freely; every
      indented line under the head belongs to this task.>

## Verification plan
depth: <fill:none | checks | scenarios>
reason: <fill:one line — why this depth fits this item (required even for none)>
env: <fill:environment recipe id, or none>

### <fill:check-id — lowercase slug, unique>
- proves: <fill:one plain sentence — what is TRUE of the product when this passes, in the owner's terms, readable with the check hidden. "With --quiet, count prints nothing at all", never "exit code is 0">
- traces: <fill:the written requirement this check defends — PRD deliverable / user story / design decision>
- covers: <fill:the task id(s) this check proves, comma-separated (t1, t3) — or leave blank for a whole-item check like a suite run>
- mode: <fill:command | interaction | inspection>
- scenario: <fill:the real steps, concretely — commands verbatim; UI steps as a user would take them>
- run: <fill:optional — ONE shell command whose exit code decides this check; the kernel runs it. Join steps with &&. Omit when a person or a subagent must judge it>
- expect: <fill:falsifiable pass condition — exact output/state, never "works correctly". Optional ONLY if this check carries a rubric instead>
- rubric: <fill:optional — delete this line and its bullets unless the check needs judging criterion by criterion>
  - <fill:one criterion, stated so it can come back MISSED>
  - <fill:another — as many as the check genuinely needs, no quota>
- source: <fill:optional — `library` when you cited this check from the repo verification library instead of writing it. Delete this line for a check you authored; entries already marked `standing` were attached by the kernel, leave them alone>
