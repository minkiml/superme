---
name: triage
description: Triage a fresh work-item — confirm its kind (implementation vs research), right-size its scope and scale, propose its deliverable, and author the brief the plan phase starts from. Use when a work-item is in its triage phase or the owner asks to triage or classify a new item; not for planning the approach or for capturing a brand-new idea.
argument-hint: "[work-item-id]"
category: workspace
---

# Triage a work-item

Decide WHAT this item is — its kind, how much of the ask belongs here, how much it is worth, and
which deliverable it serves. `artifacts/brief.md` is where that decision is written down, so a cold
plan session can start from it alone; the phase is the judgment, and the brief is its record.

Nothing here decides HOW. The approach is plan's job, and plan can only find a better answer than
yours if you leave it one.

## Step 1 — Read what exists

- **`item.md`, and everything in `preliminary/`** — the handoff brief is why this item exists; the
  title alone lost the discussion that shaped it.
  - **`preliminary/` is read-only** — it is provenance. A thin or absent brief is not yours to
    repair; say so in `brief.md`'s `## Problem` and work from `item.md` alone, rather than
    inventing the context it should have carried.
- **`general/project-prd.md`** — skim the deliverables list. You name one in step 4.
- **`read_decisions`** — the choices the owner has already ruled on. An item whose subject is
  already settled is smaller than it looks, and sometimes moot: say which `D-NNN` covers it in
  `brief.md`'s `## Problem` and size it against the ruling, not against the open question it was
  filed as.

Done when you can say in one sentence what this item wants. If the item is not in `triage`, stop and
say so.

## Step 2 — Classify the kind

| kind | what it is | typical |
|---|---|---|
| `implementation` | changes code | bug fix, new feature, an improvement |
| `research` | a dedicated deep investigation into a question | audit (security, broken behaviour, optimization) · learning from another codebase · finding what causes a bug |

- **Mixed intent** → propose `research` FIRST, and branch the build off as a `spawn` follow-up. A
  mixed item stalls at whichever pipeline it isn't.
- **Genuinely torn** → pick the safer kind (research over a mixed item) and say why in the brief.
  You make the call; the gate is where the owner disagrees with it.
- **Already filed under a kind** → your preamble says so. Agreeing is silent: record that kind and
  move on. Disagreeing is not yours to settle — end the run with
  `report_completion(machine.outcome='needs_user')` naming both kinds and what you read that
  disagrees. `set_triage_classification` refuses the contradicting kind until the owner answers.

**A research item also needs its FAMILY**, with its own one-line reason. It decides what counts as
an answer — the method the investigation follows and the shape of the record it writes — so an item
that reaches investigate without one arrives with neither:

| family | the question it answers |
|---|---|
| `audit` | is this surface sound? coverage, performance, logic, features, bugs |
| `refactoring` | this code is hard to work in — what shape should it be? |
| `housekeeping` | what has gone stale? comments, dead code, unused declarations, anything that shouldn't be there |
| `security` | what is exposed? risks, unsafe smells, unsanitized or junk data |
| `study` | how does someone else do this, and what should we take? |
| `deep-diagnosis` | what is the mechanism behind a behaviour we cannot explain? |

Pick by the QUESTION, not the subject: reading another project to find OUR bug is `deep-diagnosis`,
not `study`, and a sweep that happens to turn up a bug is still an `audit`.

## Step 3 — Size it

Two judgments, on different axes:

- **Scope — what belongs in THIS item.** Several independent deliveries → keep the core here, branch
  the rest off with `create_inbox_item` (relation `spawn`), each with its own `work_kind` — you have
  read the whole ask, so leaving that unset on a split you yourself scoped wastes what you know.
  Don't split what one session can deliver; an extra item costs real tax.
- **Scale — how much content the work is worth.** `small` or `standard`, with the one-line reason
  the owner argues with. It rides every later phase: `small` makes plan, build, vet and review read
  narrow and write short. The pipeline never changes — a small item still gets its branch, its
  tasks and its merge.
- **Fan-out — does the surface DIVIDE?** Research only, and a different question from scale: a
  bounded surface can still be real work. Whole-repo sweeps split across subagents by default, so
  say nothing and that stands. Set `fanout: "bounded"` when you have looked and it does not divide
  — one folder, one subsystem, one thread's worth. Investigate then follows your call, and the
  review gate judges the run against it instead of against the family default.

  Say it in the FIELD, not only in `scale_reason`. A judgement that lives in prose is one no
  reader has, and the gate will report the run as having failed to split — blaming it for doing
  exactly what your brief told it.

## Step 4 — Name it, and record the classification

- **Title** — the board's line. A few words naming the change, under 60 characters, no period:
  "Add dark mode support", not "I keep working at night and the white background hurts…". You are
  the first reader of the whole ask, so fix a weak one here — a hurried capture often pastes the
  request itself. Pass a good title back unchanged.
- **Deliverable** — exactly one of: the parent's (a branched-off child usually inherits it) · an
  existing PRD deliverable · a NEW one named in prose for the owner to confirm (never append it to
  the PRD yourself) · none (standalone chore).

Then call `set_triage_classification` with all of it — kind (plus the family and its reason, on a
research item) from step 2, scale and its reason from step 3, and the deliverable only when it is an
EXISTING PRD slug.

**Prose alone is not a record.** Every later phase reads these fields off the item, not out of this
chat, and the gate's red row does not stop the item advancing — nothing downstream recovers what
you only said.

## Step 5 — Author the brief

`scaffold_artifact(item_id, "brief")`, then fill its slots: the PROBLEM, the classification with its
reasons, and the context a cold session needs (pointers into `preliminary/` and the repo, not
copies).

`## Problem` states what is wrong today, never the fix. A capture usually arrives solution-shaped
("add a `--date` flag"); name the symptom underneath it ("every expense is stamped today, so a
late-entered receipt falls in the wrong month"). The fix written here spends plan's whole job before
it starts — a plan handed an answer implements it, and never finds the better one.

## Step 6 — Write the user-facing report

Fill `templates/report-triage-template.md` and hand the whole body to `file_phase_report`. It owns
the path and refuses a report with an unfilled slot left in it — never write the file yourself. It
is the OWNER's — they accept or re-shape what you decided; the kernel and the plan phase read
`brief.md`. A re-run overwrites it: the report always describes the item as it stands NOW.

- **Every line traces to `brief.md`** — a projection of it, never a place for a new fact.
- **The scope table is the point of it** — in/out is what the owner accepts or sends back.
- **`**Workflow:**` names the SAME kind you recorded in step 4.** The drilldown reads the kind off
  the item; this line is for whoever reads the brief on its own, and disagreeing with the record is
  the one thing it can get wrong.
- **`## From you` is theirs.** Copy the heading and its two empty labels through; write nothing
  under them. The owner's editor is that section's only writer, and plan treats whatever lands
  there as authority — filling it would hand plan an instruction nobody gave.
- **Keep the blank line between `**Label:**` blocks** — without it markdown folds them into one
  paragraph and the labels render mid-sentence.

**Tone and style when writing to user-facing report**
- Plain, concise, easy language. Fewer words wins. No verbosity.
- Keep the report short, clear, and to the point.
- This is where the item is explained, not where it is defended.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Prefer a table to a paragraph whenever the content is pairs or a list of comparable things.
- Use bullets and numbered lists to organize information if there is more than one point.
- Delete a block you have nothing real for — an absent section reads better than "none".

## Reporting the run

`report_completion` says why THIS RUN stopped. A run is one invocation of you — it is not the phase.

- **`success`** — classification recorded, brief authored, report filed. Not "accepted": that is
  the owner's call at the triage-exit gate.
- **`partial` · `blocked` · `clean_noop`** — when one of those is the truer word.
- **`needs_user`** — rare. A torn call is made and recorded (step 2), never paged out; use this
  only when there is nothing to classify at all.

**The outcome is all the kernel reads**, so a kernel-fired run always declares one; a run that
skips it is recorded as undeclared. **A conversation does not** — a chat turn owes no report.
Report again only when the item's state changes.

**Output style to report_completion.**
- Plain, concise, easy language.
- Keep your response short, clear, and to the point.
- Do not use more than 30 words.

## Pitfalls

- **Starting the plan** — triage decides WHAT this item is, not HOW to do it.
- **Over-triaging a small ask** — a one-line fix does not earn a three-paragraph problem statement,
  a split into three items, or a scope table padded to fill its rows.
