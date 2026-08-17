---
name: itemize
description: Turn an approved research report's proposed work into inbox items — file each non-duplicate proposal as a spawn the owner can push or drop, and record what was filed. Use when a research work-item's review gate is approved; not for capturing an idea mid-conversation (use create-inbox-item) or for launching an onboarding cohort (that is project-init and retrofit's itemize_and_launch).
argument-hint: "[work-item-id]"
category: workspace
---

# Itemize an approved research report

A research item's findings are half of what it owes; the work they imply is the other half. This run
is where that work stops being a paragraph in a report and becomes something the owner can act on.

**Filing is not launching.** Every item you file is a `spawn`, and a spawn sits in the inbox until
the owner deliberately pushes it. The inbox IS their decision surface — push it, edit it, delete it —
and triage plus its gate still stand between any item and a line of code. So you do not need
permission to file, and this run has no interactive surface to ask on: a proposal left unfiled here
is a proposal lost.

## Step 1 — Read the proposals against the live board

`artifacts/review.md` `## Proposed work` is the list. Check each one against what already exists —
`read_dev_log`, the roadmap, open inbox items.

A proposal that duplicates live work is **not filed**. Name the item it duplicates in step 3; a
second ticket for work already on the board is noise the owner has to clean up.

## Step 2 — File what survives

For each non-duplicate proposal, `create_inbox_item` with `spawned_from_item` = this item and
`relation: "spawn"`, and a brief carrying what a cold triage session needs: what was found, why this
work follows, and where the evidence lives.

- **File what the report proposed** — you are not re-scoping it. A proposal too vague to file is
  filed as it stands with its vagueness visible, not sharpened by you into something the report
  never said.
- **Carry the proposal's own typing into `work_kind`.** `## Proposed work` types each entry —
  `implementation` where the deliverable is changed code, `research` where it is an answer or a
  decision. Pass that verbatim; never re-decide it, and never leave it unset on a typed proposal.
- **`spawn` is the only legal relation here.** Never `itemize_and_launch` — that is onboarding's
  direct-mint path, and from here it would start building a conclusion nobody approved.
- **More than a handful is itself a finding.** If the report proposed eight things, file them and
  say so in step 3: a research item that fans out that wide usually needed consolidating, and the
  owner should see that in one line rather than in eight tickets.

## Step 3 — Record what happened

Fill `artifacts/review.md`'s **Owner's decision** line: what you filed, with inbox ids, and what you
skipped as a duplicate and of what. The gate reads this line back, and a research item whose
proposals vanished without a trace is the failure this line exists to catch.

A proposal you did not file for any other reason is recorded here too, with why. Then say what was
filed and stop.

## Chat response style
- Use plain and easy language.
- Keep your response short, clear, and to the point.
- Use bullets or numbered lists to organize information if there is more than one point.

## Reporting the run

`report_completion` says why THIS RUN stopped.

- **`success`** — every non-duplicate proposal is filed and the line records it.
- **`clean_noop`** — the report proposed nothing, or every proposal duplicated live work. Both are
  real outcomes; say which in the line.
- **`needs_user`** is not for filing decisions. Filing is yours. Reserve it for a proposal you
  cannot file at all — one whose brief you cannot write because the report does not say what the
  work is.

**Output style to report_completion.**
- Plain, concise, easy language. Fewer words wins.
- Keep your response short, clear, and to the point.

## Pitfalls

- **Waiting for permission that is not coming.** This run has no human on the other end; the inbox
  is where the owner decides, and it is one push away.
- **Rewriting the proposals** — you file what the report proposed. A proposal that needs reshaping
  is filed as written, and reshaped at its own triage.
- **Filing a duplicate anyway** — checking and then filing regardless makes step 1 ceremony.
