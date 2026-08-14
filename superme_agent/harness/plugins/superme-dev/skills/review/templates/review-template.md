# Review Agent-facing Report

**Delivered:** <fill:1 line — what actually shipped. The kernel writes this into the landing commit, so say it the way the project's permanent history should read: what the code now does, never what the item was called>

## Change inventory
| surface | change | tasks |
|---|---|---|
<fill:one row per touched surface — the file or module · what changed there · the plan task ids that own it>

<fill:1 line — what was deliberately NOT touched, where that is worth stating: signatures, CLI surface, persisted formats, neighbouring modules>

## Against our own decisions
<fill:0..n bullets — where this work departs from what `decisions.md` / `architecture.md` already record: the decision, quoted or named, and what the code now does instead. A departure is not automatically wrong — a recorded decision can be outgrown — but it is the owner's to ratify, and an unnamed one lands as a silent precedent. Write `Nothing departs from the recorded decisions.` when the work sits inside them; the sentence is the finding, not filler>

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
