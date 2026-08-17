---
name: create-inbox-item
description: Author one crisp inbox ticket from a discussion or a decision to propose a real work or branch work off. Use when a general chat and discussion need to turn into real work, when the user says 'ticket this' / 'itemize this' / 'put this in the inbox' / 'make a task', or when an agent decides to spin adjacent work out of the current item. Do NOT use to push an inbox item into a work-item (that's the owner's triage), to edit an existing item, or to capture an operational learning (that's the learning pipeline).
argument-hint: "[what-to-itemize]"
category: general
---

# Create inbox item

Turn real work that surfaced in conversation into an Item Inbox ticket the owner can later push into a
work-item — by creating a new item, or by augmenting an existing one that already covers it. You only
author the ticket; you never start the work itself.

## Step 1: Check the Item Inbox first

Call `read_inbox` and scan the open items for one that already covers this work (same target + intent).

- **No match** → it's a new ticket → step 2.
- **Match, already captures what we discussed** → do NOT write. Reference it and stop:
  "I found item #N — \"<title>\" — it already covers this."
- **Match, but this discussion adds something new** → augment it → step 3.

## Step 2: Create (new ticket)

Compose the `body` in this exact shape (read a file only to pin down a reference — don't go implement):

```
**What:** <the work to do, 1–2 sentences — concrete and actionable>
**Why:** <the motivation / the problem it solves>
**Context:** <the load-bearing decisions, constraints, considerations, or options weighed in the discussion — bullets>
**References:** <work-item ids · file paths · doc names · prior tickets — omit the line if none>
```

Rules:
- **On-point, not verbatim.** Capture the conclusions and the essential context that is only related
  to this ticket, not the back-and-forth.
- **No invention.** Do NOT create and/or add any new contents/ideas that were never discussed.
- **Title** = a short, specific headline (what+where), not a sentence and not a slug.
- **kind** = `todo` for a change to make, `idea` for a proposal to consider, `question` for something
  to resolve, `note` otherwise (default `note`).
- **work_kind** = `implementation` if the ticket's deliverable is changed code, `research` if it is
  an answer, a report or a decision. Set it whenever the discussion settled which; omit it when the
  discussion genuinely did not, and say in your reply that you left it for triage.

Then call `create_inbox_item` with the title/body/kind AND the **handoff-brief fields** — the tool
scaffolds `handoff-brief.md` next to the ticket, and these four fields are its content. Fill them
NOW, while the discussion is hot: the future work-item's triage session cold-starts from this brief,
and an empty one throws away exactly the context you currently hold. High-level only — NO plans, no
implementation detail:

- `background` — the problem/story: why this was raised, in the discussion's own terms.
- `discussion` — what was discussed and concluded so far (conclusions, not the back-and-forth).
- `direction` — the high-level direction or options on the table, with any leanings.
- `constraints` — constraints, things tried-and-failed, explicit out-of-scope.

Skip a field only when the discussion genuinely produced nothing for it (a bare quick capture may
fill just `background`). Body ≠ brief: the body is the crisp ticket; the brief carries the context
behind it.

**Branch-off from a work-item session:** pass `spawned_from_item` (the current item's id) +
`relation` — `blocking` (parent must wait; auto-pushes and pauses the parent) · `parallel`
(independent but gates the parent's completion; auto-pushes) · `spawn` (speculative follow-up;
waits in the inbox for the owner's push). Omit both for ordinary discussion tickets.

## Step 3: Augment (existing item)

You reach here when `read_inbox` matched an item that covers this work but is MISSING something this
discussion just added. Never re-file it and never edit its existing text.

1. **Isolate what's genuinely new** — the specific facet(s) the existing item doesn't already state
   (a new requirement, constraint, edge case, reference, or a changed decision). If, on a closer look,
   the item already covers it, stop and just reference it (Step 1's "already covers" case) — don't append.
2. **Write the `addition` as a short, self-contained note of only that new content** — same on-point /
   no-invention rules as Step 2. It lands under the existing text below a divider, so make it readable
   on its own: lead with the facet (e.g. "Also: …" or "Update: …") and include any new reference. Do
   not restate what the item already says.
3. **Call `append_inbox_item(item_id, addition)`** — one call per item. It preserves the existing
   content and marks the item agent-touched (origin gains `agent`).
   - Pass `brief_field` when the addition belongs under a specific handoff-brief section —
   `background`, `direction` or `constraints`. It defaults to `discussion`, and a constraint
   mirrored into Discussion summary is one the triage session will not find where it reads.

## Step 4: Confirm

Reply with ONE short line matching the case, then return to the discussion — no recap of the body:

- **Created:** `Filed inbox item #<id> — "<title>". In your Item Inbox to review & push into a work-item.`
- **Augmented:** `Updated inbox item #<id> — "<title>" (added: <the new facet, ≤8 words>).`
- **Already covered:** `Already tracked — inbox item #<id> — "<title>". Nothing new to add.`

## Scale

Don't size-police a ticket. If the work is large, file it as ONE ticket — the workspace handles any
decomposition when the item is worked. File separate tickets only when the discussion covers
genuinely distinct, unrelated pieces of work.

## Examples

**Create.** Discussion concludes the run-trace header should show a status pill. You call `read_inbox`
— nothing matches — so it's a new ticket:

- title: `Status pill on run trace header in Activity dashboard`
- kind: `todo`
- body:
  ```
  **What:** Add a color-coded status pill to a run's execution-trace header: green=done, red=errored,
  amber=running.
  **Why:** Opening a trace gives no at-a-glance outcome — the user must scroll to tell if it finished.
  **Context:** Source the pill from the run's terminal status, not the last visible trace event.
  **References:** web/frontend/src/features/activity/RunTraceModal.tsx
  ```
- confirm: `Filed inbox item #58 — "Status pill on run trace header in Activity dashboard". In your Item Inbox to review & push into a work-item.`

**Augment.** Later, a discussion adds that the pill should also show elapsed duration. `read_inbox`
now matches item #58 — which doesn't mention duration — so you append that one facet instead of filing
a duplicate:

- `append_inbox_item(58, "Also: show elapsed run duration next to the status pill, for at-a-glance run length.")`
- confirm: `Updated inbox item #58 — "Status pill on run trace header in Activity dashboard" (added: elapsed duration).`

## Common pitfalls

1. **Skipping the Inbox check** — always `read_inbox` first; augment an existing item, don't re-file it.
2. **Dumping the transcript** — synthesize; the body is conclusions + on-point context, not the chat.
3. **Inventing** — include only what was actually discussed.
4. **Starting the work** — you author the ticket only; implementation happens later in its work-item.
5. **Using Write/Edit** — `create_inbox_item` / `append_inbox_item` are the only sanctioned writes.
