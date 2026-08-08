---
name: itemize
description: Turn an approved research report's proposed work into inbox items — put the proposals to the owner, file the ones they choose, and record the decision. Use when a research work-item's review gate is approved; not for capturing an idea mid-conversation (use create-inbox-item) or for launching an onboarding cohort (that is project-init and retrofit's itemize_and_launch).
argument-hint: "[work-item-id]"
category: workspace
---

# Itemize an approved research report

Approving a research item is not a merge — it is the decision about which of its `## Proposed work`
becomes real. That decision is the owner's, one proposal at a time, and this is where it happens.

## Step 1 — Read the proposals against the live board

`artifacts/review.md` `## Proposed work` is the list. Before putting it to the owner, check
each proposal against what already exists — `read_dev_log`, the roadmap, open inbox items. A
proposal that duplicates live work is presented as a duplicate, naming the item it duplicates, not
offered as if it were new.

## Step 2 — Put them to the owner

Present the proposals as a numbered list — title, kind, one line of why now — and ask which to
file. Keep your own view to one line per proposal at most: they have the report, and this is their
call, not a case to argue.

**Declining is a complete answer.** All of them, some of them, or none — "none" ends this step and
the item closes normally with its findings intact. Nothing here is a failure path.

## Step 3 — File what they chose

For each accepted proposal, `create_inbox_item` with `spawned_from_item` = this item and
`relation: "spawn"`, and a brief carrying the research context the future triage session will
cold-start from: what was found, why this work follows, and where the evidence lives.

`spawn` is the only legal relation here, and it is the reason nothing runs on its own: a spawn
waits in the inbox for the owner's deliberate push. Research proposes work that does not exist
yet — it never launches it, and never wires an ordering it hasn't earned.

## Step 4 — Record the decision

Fill `artifacts/review.md`'s **Owner's decision** line: which proposals were adopted (with their
inbox ids) and which were declined. A declined proposal stays written where it is — it is trace,
not a lost thought — and the close gate reads this line back to confirm the decision was actually
put to them.

Then say what was filed and stop.

## Pitfalls

- **Filing what wasn't chosen** — silence is not consent; an unanswered proposal is declined.
- **Reaching for `itemize_and_launch`** — that is onboarding's direct-mint path and puts work
  straight onto autopilot; from here it would start building a conclusion nobody approved.
- **Rewriting the proposals** — you file what the report proposed; a proposal that needs reshaping
  goes back to the owner as a question, not an edit.
