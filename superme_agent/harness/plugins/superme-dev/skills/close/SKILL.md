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

`reports/report-review.md` (it names the docs this item owes), `## Design` and `## Tasks` in
`artifacts/plan.md`, the cycle reports, and the merge commit on the item record. Then read the
anchor sections you are about to touch, so you edit what is there rather than what you remember.

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

## Step 3 — Write the close report

Copy `templates/report-close-template.md` to `reports/report-close.md` and fill it:

- **What landed** — the merged change in a few lines, for someone reading the trail months later.
- **What the docs now say** — doc, section, and what it claims after your edit.
- **What was skipped and why** — a denied authorization, an op you judged premature, an unfinished
  task. Silence here is how a gap becomes a surprise three items later.

Anything worth doing later becomes `create_inbox_item` (relation `spawn`) — never an implicit
"someone will notice".

## Step 4 — Report

Call `report_completion`. The kernel takes it from there: worktree removed, sessions retired, item
marked done. You never advance or complete the item yourself.

## Pitfalls

- **Writing intent instead of outcome** — these docs describe main as it stands. "Will support X"
  is always wrong here.
- **Editing an anchor doc directly** — refused at the write boundary, and it would skip the
  validation that stops a doc acquiring a dead file reference.
- **Applying a denied authorization** — the owner's no is a decision, not an obstacle. Record the
  gap instead.
