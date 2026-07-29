---
name: close
description: Write a work-item's close report — what landed, the verified facts, what was skipped and why. Use when a work-item reaches its close phase after the merge decision; not for the merge itself (owner-gated at review).
argument-hint: "[work-item-id]"
category: workspace
---

# Close out a work-item

Write the record this item leaves behind, then propose the close. Every fact is checked against
ground truth — files must exist, the merge commit must be real — so write only what is true of the
repo as it stands.

## Step 1: Write the close report

Copy `templates/report-close-template.md` (this skill's folder) to `reports/report-close.md` and
fill it. Derive every line; assert nothing:

- **What landed** — the merged change in a few lines, for someone reading the trail months later.
- **Facts** — the real changed-file list (`git diff --name-only <base>..<branch>`), what tests ran,
  and the recorded merge commit (item.md frontmatter) or `none` if it never merged.
- **What was skipped and why** — a denied authorization leaves a KNOWN gap; name it. Silence here
  is how a gap becomes a surprise three items later.

## Step 2: Reconcile loose ends

Unfinished `## Tasks` boxes, spawned children still open, follow-ups found late: name each and
where it went — a spawn item, settled with the owner, or genuinely dropped. Anything worth doing
later becomes `create_inbox_item` (relation `spawn`); nothing is left implicit.

## Step 3: Propose the close

Bank a final checkpoint if the session ran long, then call `propose_close(item_id)`. It runs the
kind's mechanical close criteria — a red one comes back as an itemized fix list; repair and
re-propose. All green pages the owner; completion is their promotion, never yours. If a blocking
parent waits on this item, say so — completing it is what resumes the parent.

## Pitfalls

- **Rounding facts up** — every changed-file path and the commit hash are checked; a fabricated
  entry bounces with an itemized rejection.
- **Closing over open children** — completion is refused while a blocking/parallel child is
  non-terminal; reconcile children first.
- **Reporting the plan instead of the outcome** — this report describes what is now true of main,
  not what was intended.
