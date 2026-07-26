---
name: close
description: "Draft a work-item's closeout record — verified facts (changed files, tests, merge commit), summary, artifacts worth keeping. Use when a work-item reaches its close phase after the merge decision; not for the merge/readiness work (use review) — completing the item is the owner's action, never yours."
argument-hint: "work-item id (optional — defaults to the bound item)"
category: workspace
---

# Close out a work-item

Write the record this item leaves behind. The kernel VERIFIES the facts against ground truth
(files must exist, the merge commit must be real) and rejects fabrication — write only what is
true of the repo as it stands.

## Step 1: Draft the closeout

`scaffold_artifact(item_id, "closeout")`, then fill:

- **Summary** — what this item changed and why it mattered, a few lines, written for someone
  reading the trail months later.
- **Facts** (the fenced yaml) — `changed_files`: the real list (from `git diff --name-only
  <base>..<branch>` via Bash) · `tests_run`: what actually ran · `merge_commit`: the item's
  recorded merge commit (item.md frontmatter), or empty if it never merged.
- **Artifacts** — bullet the item-folder paths worth keeping (plan, validation, readiness,
  findings); the folder persists after completion.

## Step 2: Reconcile loose ends

- Unfinished `## Tasks` boxes, spawned children still open, follow-ups discovered late: name each and
where it went (a spawn item, struck with the owner, or genuinely dropped).
- A clean closeout has no silent leftovers — anything worth doing later becomes `create_inbox_item`
(relation `spawn`).

## Step 3: Propose the close

- Bank a final checkpoint if the session ran long, then call `propose_close(item_id)`. It runs the
kind's mechanical close criteria (required artifacts clean, closeout claims verified, evidence fresh,
merged-or-logged, knowledge row resolved, children terminal) — a red criterion comes back as an
itemized fix list; repair and re-propose.
- All green pages the owner at the close gate; completion itself is their promotion, never yours. If a
blocking parent is waiting on this item, say so — completing it is what resumes the parent.

## Pitfalls

- **Rounding facts up** — the kernel checks every changed-file path and the commit hash;
  a fabricated entry bounces the closeout with an itemized rejection.
- **Closing over open children** — completion is mechanically refused while a blocking/parallel
  child is non-terminal; reconcile children first.
