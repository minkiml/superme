<!-- Agent-facing review record: artifacts/review.md — written at review, BEFORE the owner's report.
     Its readers are machines and later agents: a revision build cycle, that cycle's vetter, close,
     the landing commit, and whoever touches this code months from now. State the record; the
     persuasion belongs in reports/report-review.md. Every line traces to plan.md or a
     build-vet-<n>.md — no new facts. Overwrite in place on a re-write, except `## Revision rounds`,
     which only ever gains a block. -->
# Review Agent-facing Report

**Delivered:** <fill:1 line — what actually shipped. The kernel writes this into the landing commit, so say it the way the project's permanent history should read: what the code now does, never what the item was called>

## Change inventory
| surface | change | tasks |
|---|---|---|
<fill:one row per touched surface — the file or module · what changed there · the plan task ids that own it>

<fill:1 line — what was deliberately NOT touched, where that is worth stating: signatures, CLI surface, persisted formats, neighbouring modules>

## Settled — do not re-open in a revision cycle
| decision | who | when |
|---|---|---|
<fill:one row per question this item closed — the decision · owner or agent · the phase it was made in. From the brief and plan.md `## Decisions & clarifications`>

A revision may overturn any of these — only on new owner feedback, and only by saying so under
`## Revision rounds`. Re-deciding a settled question without that is how an item loops forever.

## Proven vs taken on trust
| claim | basis |
|---|---|
<fill:one row per claim the merge rests on — the check id and how it ran (kernel-run / agent-attested), or **not covered** and why. Do NOT restate the evidence ledger; cite into it. The row it structurally cannot hold — the claim nothing covers — is the one that most needs to be here>

## Risks surviving merge
<fill:0..n bullets — what is still true once this lands and could bite later: paths never exercised, precedent this now sets, assumptions still standing. "None" only when it is>

## Revision rounds
_None. First review._
