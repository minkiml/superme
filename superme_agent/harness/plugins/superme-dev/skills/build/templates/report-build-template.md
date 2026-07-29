<!-- Build report: reports/report-build.md. Keep it ≤ 1 screen; tables over paragraphs.
     Every line traces to the cycle reports (build-vet-<n>.md §Built / §Validation) — no new
     facts. Rewrite it at each cycle's end, overwriting in place, so the final cycle's version
     summarizes the whole loop; fill ## Changed since on re-writes, delete it on the first. -->
# Build — {title}

**Outcome:** <fill:1 line — what was delivered, over how many cycles>

**What was built:**
<fill:up to 6 bullets, task → what changed>

**Validation (internal checks):**
| check | result |
|---|---|
<fill:one row per check — final state, verbatim results>

**Gaps & assumptions:** <fill:one per line, incl. authorizations requested — delete the line if none>

**Stats:**
| cycles | files ±lines | commits |
|---|---|---|
<fill:one row of counts>

## Changed since v<n>
<fill:re-writes only — the compact delta from the previous version>
