---
name: close
description: Finalize a merged work-item's knowledge — update the anchor docs, record the change-log entry, and write the close report. Use when a work-item reaches its close phase; not for the merge itself (owner-gated at review).
argument-hint: "[work-item-id]"
category: workspace
---

# Close out a work-item

The code is locked — review's approval merged it, and nothing downstream can change it. So this run
is not about the work; it is about what the project now KNOWS. You are the only writer of the
general dev-knowledge docs. Skip a line here and they quietly describe a codebase that no longer
exists.

## Step 1 — Read what landed

`artifacts/review.md` — review's record of what landed: the change inventory, what it named the
anchor docs as owing, what is settled, and what risk survived the merge. Then `## Design` and
`## Tasks` in `artifacts/plan.md`, the cycle reports, and the merge commit on the item record.
Then read the anchor sections you are about to touch, so you edit what is there rather than what
you remember. (`reports/report-review.md` is the owner's, not yours — it argues a decision they
have already made.)

## Step 2 — Update the anchor docs

`apply_knowledge_delta(item_id, ops)` — one call, all ops, validated then written.

- **Write what is TRUE OF MAIN NOW**, not what the item intended. A section you touch should read
  as if its reader has never heard of this work-item.
- **One op per section.** `update` replaces a body · `append` adds to it · `supersede` replaces text
  a decision has overtaken · `rename_section` fixes a stale heading.
- **A granted authorization's ops are yours to apply** — the owner said yes at review, and here is
  where that becomes a doc change. A DENIED one leaves a known gap: skip it, and name it in Step 3.
- **Nothing doc-worthy? Call nothing.** A no-op close is a real outcome; Step 3 says so.

A refusal is itemized and writes nothing — fix the named op and call again.

## Step 2b — Write in whatever vet nominated for the verification library

`read_verification_library(item_id)` returns this repo's library plus any check vet nominated here,
rendered as a ready entry. Add each as one `append` op on doc `verification`, section `Available` —
verbatim, unless it still names something item-specific, in which case restate it about the repo.

Nothing nominated? Then nothing to do — most items add nothing, and an entry added to look
productive taxes every later plan that inherits or reads it.
Entries land as **available**; only the owner promotes one to standing.

## Step 3 — Write the close report

Write user-facing report, `reports/report-close.md`, from `templates/report-close-template.md` and fill it. The template's
`<fill:…>` slots and its `<!-- … -->` notes are both instructions to you: replace the slots, drop
the notes. Neither belongs in the file you write.

This is the last thing the owner reads about this item, and what they will find if they come back
to it in six months. Write what is TRUE OF MAIN NOW, not what the item set out to do.

- **`**Summary:**` in one line** — what is now true and what, if anything, follows. The closed card
  shows it alone.
- **`## What's now true`** — for a reader with no memory of this item: what main does that it
  didn't, and who can rely on it.
- **`## What the project now records`** — doc, section, and what it claims after your edit. When
  nothing needed changing, say so AND why: "no document described this behaviour" is itself worth
  the owner knowing, and is often why it drifted.
- **`## Left undone on purpose`** — a denied authorization, an op you judged premature, an
  unfinished task, each with whether it was filed. Silence here is how a gap becomes a surprise
  three items later.

Anything worth doing later becomes `create_inbox_item` (relation `spawn`) — never an implicit
"someone will notice".

**Tone and style when writing to user-facing report only**
- Plain, easy language. Fewer words wins.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a prose field rather than filling it with "none" — an absent block reads better.


## Step 4 — At completion

Call `report_completion`. The kernel takes it from there: worktree removed, sessions retired, item
marked done. You never advance or complete the item yourself.

## Chat response style
- Use plain and easy language.
- Keep your response short, clear, and to the point.
- Use bullets or numbered lists to organize information if there is more than one point.

## Pitfalls

- **Writing intent instead of outcome** — these docs describe main as it stands. "Will support X"
  is always wrong here.
- **Editing an anchor doc directly** — refused at the write boundary, and it would skip the
  validation that stops a doc acquiring a dead file reference.
- **Applying a denied authorization** — the owner's no is a decision, not an obstacle. Record the
  gap instead.
