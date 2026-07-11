---
name: standup
description: "Compose a development standup from the activity log and work-item state — what moved, what's left, what's next. Use when the owner asks to catch up on development progress: a standup or recap, 'what did we do yesterday', 'where are things', 'what's next', or a count/provenance question about past activity."
argument-hint: "the question (optional — e.g. a day, or a work-item to focus on)"
category: general
---

# Standup

Answer the owner's catch-up questions with a composed, human-readable brief about the **real work** —
what each item is about, what was actually done, what remains — then offer to resume. The owner cares
about work progress, not bookkeeping: never tally queue housekeeping (captured / merged / dropped /
pushed). Compose from the activity **LOG** (`dev_log`) ⋈ **work-item briefs** ⋈ a forward suggestion.
Selective briefs, never a dump.

## 1. Classify

- **Retrospective** ("what did we do yesterday / on <date>") → past-window recap → step 2.
- **Current state** ("what are we up to / where are things") → what moved + where it stands + next move → step 2.
- **Analytics** (a count or provenance question) → answer from the LOG only → skip to step 5.

## 2. Pull the LOG, split work from plumbing

Call `dev_log`. Pass `day` for date questions (resolved in the owner's local timezone) — never build a
date filter yourself. Retrospective: `dev_log(day="yesterday"|"YYYY-MM-DD")`. Current state:
`dev_log(day="today")` + `dev_log(limit=30)`. One item: `dev_log(item_id="<id>")`.

Sort the events:

- **Work-progress** (the answer): `plan.start`/`plan.end`, `phase.advance`, build/eval, `item.drop`,
  recorded decisions. Collect the distinct `item_id`s.
- **Queue housekeeping** (background): `inbox.add`/`push`/`merge`/`drop` — do not enumerate.

*Done when:* you have the work-progress `item_id`s.

## 3. Brief each item (targeted, not a dump)

For each `item_id`, read `work-items/<id>/`:

- `item.md` frontmatter → `title`, `phase`, `status`.
- `artifacts/tasks.md` → `done/total` and the **first unchecked** `- [ ]` task (= next step).
- `artifacts/plan.md` → **only** the `## Approach` first 1–2 sentences (the highlight).

Read further only if the owner drills in. *Done when:* each item has a title, a plain-English state, a
highlight, and a next step.

## 4. Compose

Write for a human who doesn't remember slug ids. Follow this shape:

```
**Today (June 23)** — you worked on 2 items:

1. **<Title>** — <what it is + the highlight of what was done>. Plan ready, awaiting your approval;
   5 of 6 tasks left, next: <first open task>.
2. **<Title>** — <…>. In build; 2 of 8 tasks left, next: <…>.

New idea captured: "<one new note, by content>" — want me to triage it?

You can pick these up from the workspace, or want me to resume one from here? Anything else?
```

- **Lead with the count**; one numbered bullet per item, anchored on its plain-language `title` (never
  a slug id — cite a short id only as a trailing handle when titles collide).
- Each bullet = **topic + what was done + what's left / next step**.
- **Translate state**, never raw enums: `plan_design/queued`→"not planned yet" ·
  `plan_design/in_progress`→"being planned now" · `plan_design/waiting`→"plan ready, awaiting your
  approval" · `build_eval/*`→"in build" · `done`→"done".
- New incoming ideas get one forward-looking line by content.
- **Explain any number** cited; **close with a resume offer**.

If nothing moved, say so plainly ("Quiet day on the actual work") and offer the board state.

## 5. Analytics

Only here do mechanical LOG counts / provenance belong. Answer **story-first**, the number
parenthetical, from events + `meta` (`meta.origin`, `meta.merged_from`, `actor`) — not artifacts.
e.g. "It was created by merging two earlier inbox notes, on June 22."

## Common Pitfalls

1. **Leaking slug ids / bare inbox numbers** — anchor on titles; explain every number.
2. **Tallying housekeeping** — "N merged, M dropped" is plumbing; foreground real work.
3. **Dumping artifacts** — approach line + first open task, never whole files or the whole LOG.
4. **Raw enums** — translate `plan_design/waiting` to plain English.
5. **Your own date filter** — pass `day` to `dev_log`; naive UTC dates miss events.
