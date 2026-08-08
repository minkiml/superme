<!-- Triage brief — copy to reports/report-triage.md. TWO readers: the owner, and the plan agent,
     which cold-starts from this. Every other user-facing report is the owner's alone.
     Plain words throughout — this is where the item is explained, not where it is defended.
     Every line traces to brief.md; no new facts.
     NEVER restate the item's kind or id — both are chips on the drilldown header, three inches
     above. A line that repeats a badge is spent budget.
     Delete any block you have nothing real for; an absent section reads better than "none".
     On a re-run, overwrite in place: this report always describes the item as it stands NOW.
     KEEP THE BLANK LINE BETWEEN EVERY **Label:** BLOCK — without it markdown folds them into one
     paragraph and the labels render mid-sentence. -->
# Triage User-facing Brief

**Workflow:** <fill:Implementation or Research — the kind you recorded with set_triage_classification, capitalised. It decides which machinery the item runs on, so it must MATCH what you recorded>

**Category:** <fill:one word — Bug · Feature · Chore · Question · Refactor>

**Background:** <fill:OPTIONAL, delete unless it genuinely helps — one or two lines of where this came from. The STORY only; never a work-item id, which means nothing to the reader>

**Problem:** <fill:one line, in the owner's terms — what is wrong today. Use **Goal:** instead when nothing is broken and this is something new>

**Summary:** <fill:one line — what needs to happen. This line is what the dashboard shows while the item sits in triage, so it has to stand alone>

## What you'll get

**Current behavior:**
<fill:what happens today, concretely — the command, the click, the output. Edge cases and error conditions count: say what they do now>

**Desired behavior:**
<fill:what will happen instead, just as concretely, INCLUDING the edges — what stays the same, what errors still do, what nobody will notice. A before/after in a fenced block earns its space when the difference is visible output>

## Scope & Out of scope

| Doing | Not doing |
|---|---|
| <fill:up to 3 rows each side — Not doing is what a reader would reasonably expect under this title and will NOT get> | <fill:…> |

<fill:one line — WHY the out-of-scope column is drawn there, and what it would take to move something across. Delete if the split is self-evident>

## From you
<!-- NOT YOURS TO FILL. The owner writes this section from the drilldown's editor, which is its only
     writer; copy the heading and the two empty labels through exactly as they are and move on.
     Whatever appears under them by the time plan runs is AUTHORITY it follows, not input it weighs.
     There is no `<fill:…>` slot here on purpose: a slot would invite you to invent the owner's
     references, and an invented authority is worse than an empty one. -->

**Useful imported references:**

**Verification notes:**
