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

`read_research_proposals` with this item's id returns two lists: **File these** and **Do NOT file
these**. It has already applied the one rule you may not apply yourself — a proposal that asks the
owner a question and carries no answer is withheld, because a ticket whose ruling was never given
reads as startable and whoever picks it up will choose for them.

File only from the first list. Never file from the second, and never re-derive either list by
reading `## Proposed work` yourself.

Then check each filed-able proposal against what already exists — `read_dev_log`, the roadmap, open
inbox items. A proposal that duplicates live work is **not filed** either. Name the item it
duplicates in step 3; a second ticket for work already on the board is noise the owner has to clean
up.

If the tool reports malformed proposal blocks, file what is well-formed and name the malformed ones
in step 3 — do not repair the report.

## Step 2 — File what survives

For each non-duplicate proposal, `create_inbox_item` with `spawned_from_item` = this item and
`relation: "spawn"`.

**The four brief fields, and what each owes here.** They are the whole cold-start context: the
triage session that reads them has this item's id and nothing else. Every pointer must survive the
reader not having your context.

| field | what it owes on a spawn from a report |
|---|---|
| `background` | the one finding this proposal answers, in the report's terms — not a summary of the whole sweep |
| `discussion` | where the evidence is: **artifact file AND the section inside it**, plus the commit sha the report measured against |
| `direction` | the proposal's `delivers:` clause, carried across |
| `constraints` | the proposal's `**Default applied:**` or the owner's ruling verbatim, plus anything the report marked NOT COVERED that touches this work |

**Name the section, not just the file.** `artifacts/investigation.md` alone makes a cold reader
open a long document and search it.

**Bad and good examples** — the `discussion` field:

```
Full list in the parent item's investigation.md.
```

```
Parent item a1b2c3d4e5f6, `artifacts/investigation.md` `## What can go`, rows D–F.
Measured against commit 0f1e2d3.
```

**Write "none" only where the report wrote none.** An empty `constraints` on a proposal that stated
no default and carried no ruling is correct and reads correctly. An empty one on a proposal that
carried either has thrown away the decision the child item runs on.

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

**Name every withheld proposal and its question.** The owner cannot see an absence — without this
line a half-filed review is indistinguishable from a complete one. Write the count and the
questions: `2 of 5 not filed — awaiting your ruling on <question>, <question>`.

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
