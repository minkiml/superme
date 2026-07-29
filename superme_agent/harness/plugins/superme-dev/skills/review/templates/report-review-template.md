<!-- Implementation review report: reports/report-review.md — the surface the merge decision is
     made on. Keep it ≤ 1 page; tables over paragraphs. Every line traces to brief.md, plan.md or
     a build-vet-<n>.md — no new facts, no claim the cycle reports don't carry. On a re-write
     after a revise, overwrite in place and fill ## Changed since; delete that section on the
     first write. -->
# Implementation — {title}

**The ask:** <fill:1-2 lines — what this item set out to do, from brief.md>

**Delivered:** <fill:1 line — what actually shipped>

**Key decisions:**
<fill:up to 4 bullets — owner-made and agent-made, each with its provenance, from plan.md ## Decisions & clarifications>

**What was built:**
<fill:up to 6 bullets, task → change, from the cycles' §Built>

**Verified:**
| check | result | evidence |
|---|---|---|
<fill:the final cycle's §Verification verbatim — a ✗→✓ mark carries the cycle history>

**Open risks & grants:**
<fill:0..n — assumptions that still stand · authorizations requested and how they were decided · "none" only when true>

**Journey:**
| cycle | built | validation | verification | outcome |
|---|---|---|---|---|
<fill:one row per build⟷vet cycle>

**Stats:**
| cycles | files ±lines | commits | tokens | duration |
|---|---|---|---|---|
<fill:one row of counts — the diff shape read from git, never estimated>

**Recommendation:** <fill:1 line — merge, or what stands in the way>

## Changed since v<n>
<fill:re-writes only — the compact delta from the previous version: what the revise changed and what it means for this decision>
