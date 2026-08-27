---
name: close
description: Finalize a merged work-item's knowledge — update the anchor docs, record the change-log entry, and write the close report. Use when a work-item reaches its close phase; not for the merge itself (owner-gated at review).
argument-hint: "[work-item-id]"
category: workspace
---

# Close out a work-item

The code is locked — review's approval merged it, and nothing downstream can change it. So this run
is not about the work; it is about what the project now KNOWS. You are the only writer of the
general dev-knowledge docs, and a line skipped here leaves them quietly describing a codebase that
no longer exists.

**Close re-adjudicates nothing.** Every question about the work was answered at the last gate where
the owner could still act on the answer.

## Step 1 — Read what landed

- **`artifacts/review.md`** — the change inventory, which anchor docs it named as owing, what is
  settled, what risk survived the merge.
- **`artifacts/plan.md`** `## Design` and `## Tasks`, the cycle reports, and the merge commit on the
  item record. **A research item has none of these** — it has no plan phase, no build cycles and no
  merge. Read `artifacts/investigation.md` instead: the questions, what answered them, and what was
  left open.

Do NOT read the anchor docs yet — step 2 reads each section immediately before it edits it, and a
read taken now would be stale by then.

`reports/report-review.md` is the owner's, not yours: it argues a decision they have already made.

## Step 2 — Update what the project records

**A research item skips this step entirely.** Nothing it concluded has been implemented, so the
anchor docs — which describe what is IN the main tree — owe it nothing; the tool refuses outright.
Its conclusions already live in its own report, and they reach the docs later through the work that
acts on them. Go to step 3.

Work one section at a time, in this order. Do all of them before you call anything.

1. **Test that the section is doc-worthy.** One question: does it now read as FALSE to someone who
   has never heard of this item? Yes → it owes an edit. No → it owes nothing, and you write nothing.
   A rename, a comment pass, an internal refactor and a test-only change all come out no.
2. **Read the section as it stands right now**, from the anchor doc itself — the folder holding
   them is in your `## Current focus` block. Another item may have closed into it while this one
   was in flight, so what is on disk is what you are editing, never what `review.md` quoted and
   never what you remember.
3. **Compose the whole body the section should END with**, folding your change into the step-2 text.
   Keep every clause that is still true; a body that mentions only this item's work has deleted
   somebody else's.
4. **Pick the op.** `append` adds under what is there — the safe one, and right whenever the section
   is a list that grows. `update` replaces the body · `supersede` replaces text a decision has
   overtaken · `rename_section` fixes a stale heading.
5. **`update` and `supersede` carry `expect`: the step-2 body, verbatim.** If the section moved
   between your read and your call, the whole delta is refused and nothing is written — re-read it
   and redo steps 3–5 against the new text.

Then one call: `apply_knowledge_edits(item_id, ops)` — every op together, validated then written. A
refusal is itemized and writes nothing; fix the named op and call again.

- **Write what is TRUE OF MAIN NOW**, not what the item intended. A section you touch should read as
  if its reader has never heard of this work-item.
- **One op per section.** A second op on the same section is refused: put the whole intended body in
  the first one.
- **A granted authorization's ops are yours to apply** — the owner said yes at review, and this is
  where that becomes a doc change. A DENIED one leaves a known gap: skip it, and name it in step 3.
- **Nothing doc-worthy? Call nothing.** A no-op close is a real outcome, and step 3 says so.

**Whatever vet nominated for the verification library** goes in the same call — and **your trigger
says whether there is anything.** It counted before you were fired, so when it says vet nominated
nothing, there is nothing: do not call `read_verification_library` to confirm an answer you already
have. When it names a count, `read_verification_library(item_id)` returns this repo's library plus
this item's nominations, rendered as ready entries: add each as one `append` op on doc
`verification`, section `Available` — verbatim, unless it still names something item-specific, in
which case restate it about the repo. Entries land as available; only the owner promotes one to
standing. Most items nominate nothing, and an entry added to look productive taxes every later plan
that reads it.

## Step 3 — Write the user-facing report

Fill the report template your trigger carries and hand the whole body to `file_phase_report`. It owns
the path and refuses a report with an unfilled slot left in it — never write the file yourself. This
is the last thing the owner reads about this item, and what they will find if they come back to it in
six months.

- **Write what is TRUE OF MAIN NOW**, not what the item set out to do.
- **Nothing checks this report.** The close gate asks only whether the artifacts exist, so the
  `## Facts` rows have to come from the commands and the item record — never from memory of what the
  cycles said.
- **`## Left undone on purpose` is the one section silence ruins.** A denied authorization, an op
  you judged premature, an unfinished task — each with whether it was filed. Left out, a gap becomes
  a surprise three items later.
- **On a research item this report IS the close.** `## What the project now records` says the honest
  thing — that a research item writes no anchor docs, and what would have to happen for its findings
  to reach them.
- **Anything worth doing later becomes `create_inbox_item`** (relation `spawn`), never an implicit
  "someone will notice". Give each one a `work_kind`: a leftover task is `implementation`, an
  unresolved question or a doc gap you could not settle is `research`.

**Tone and style when writing to user-facing report**
- Plain, easy language. Fewer words wins.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a prose field rather than filling it with "none" — an absent block reads better.

## Reporting the run

`report_completion` is what releases the item: the kernel then removes the worktree, retires the
sessions, and marks it done. You never advance or complete the item yourself.

- **`success` · `clean_noop`** — the knowledge writes landed, or there were genuinely none to make.
- **Any other outcome still clears the item**, and is recorded on its permanent trail as a knowledge
  gap. Close has no authority to change anything, so there is no wall here worth holding for — say
  what did not get written and let it go.
- **No report at all is the one costly ending.** The kernel re-fires this run twice, then clears the
  item anyway and stamps it "the closing run ended without a report — the anchor docs were not
  updated". That sentence outlives the item.

**Output style to report_completion.**
- Plain, concise, easy language.
- Keep your response short, clear, and to the point.
- Do not use more than 30 words.

## Pitfalls

- **Writing intent instead of outcome** — these docs describe main as it stands. "Will support X" is
  always wrong here.
- **Editing an anchor doc directly** — refused at the write boundary, and it would skip the
  validation that stops a doc acquiring a dead file reference.
- **Applying a denied authorization** — the owner's no is a decision, not an obstacle. Record the
  gap instead.
