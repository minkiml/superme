<!-- Build report: reports/report-build.md. Keep it ≤ 1 screen; tables over prose.
     Every line traces to the cycle reports (build-vet-<n>.md §Built / §Validation) — no new facts.
     Rewritten at each cycle's end, overwriting in place: this report always describes the CURRENT
     state of the work, never a diff against the last round. The round history lives in the
     **Summary** line and nowhere else. -->
# Build User-facing Report

**Summary:** <fill:one line — what is now true, and the round history only when there is a story in it. "Done, first attempt — …" · "Done, after three rounds — the empty-ledger case took two tries" · "Done, after the rework you asked for — …". This line is what the dashboard shows while the item is building>

## What changed

| what | from | to |
|---|---|---|
<fill:one row per visible difference — the thing, how it behaved, how it behaves now. Include a row reading "unchanged" for anything a reader would expect to have moved and which didn't>

<fill:one line — what was NOT touched, where a reader might assume otherwise. Delete if nothing needs saying>

## Checked as I went
<fill:a `- [x]` line per internal check you actually ran, in the owner's words: what you exercised and what it did. Not the command — what it demonstrated. These are your OWN checks; the independent pass is vet's report>

## Work this turned up
<!-- OPTIONAL — delete this whole section unless something was actually filed. It is the only way a
     blocking spawn reaches the owner, because build runs unattended. Say whether it was FILED and
     whether this item waits on it; never print the new item's id, which means nothing to them. -->
<fill:one bullet per item filed — what it is, why it couldn't live here, and whether this item waits on it>
