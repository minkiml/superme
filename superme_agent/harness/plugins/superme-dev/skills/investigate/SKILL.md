---
name: investigate
description: Run a research work-item's investigation — set the questions and the walls, then answer them with evidence recorded in the item folder. Use when a research work-item is in its investigate phase; not for implementing anything (use build) or for drawing the conclusions (use review, at the review phase).
argument-hint: "[work-item-id]"
category: workspace
---

# Investigate (research item)

Set the **questions** and the **walls**, then answer them with receipts.

**The subject is whatever the item names** — this repo, another codebase, a library, an external body
of material, the behaviour of a running system. What never changes across those: a research item has
no worktree, your only writes are the item's own folder, and every claim you record carries the
receipt that makes it checkable by someone who was not here.

## Step 1 — Bound the sweep before you read anything

**A research item has no plan phase.** Nothing upstream states what you are answering or where you
stop — that is yours to set, and it is the first thing you write, before the code and before the
first finding. An unbounded sweep does not run out of questions; it runs out of run.

Read what the item gives you:

- **`artifacts/brief.md`** — `## Problem` is why this exists, `## Context` is what the owner already
  knows or has pointed you at, `## Classification` carries the family. A button-launched sweep has no
  brief at all: its subject, and its interest or area, are in the item's own title and description.
- **The item header** — the family (step 2) and the subject.

Then write these into `investigation.md` `## Questions`, before anything else:

1. **The questions**, as questions. Three sharp ones beat eight vague ones.
2. **The walls** — what is in scope and what is explicitly not. A whole-repo sweep says so; an area
   sweep names the area. A thread leading outside becomes an open thread, never a detour.
3. **Done** — what has to be true for this to be finished.

**Done when** someone who has not read the brief could tell, from your questions alone, what this
sweep will and will not have looked at. Get the walls wrong and it is cheap to correct now: the owner
reads them at the gate, and everything you record afterwards is read against them.

## Step 2 — Know which investigation this is

**Triage named the family and it is in your item header** — it decides what counts as an answer, and
the report is read against that bar. On an older item that carries none, name it yourself from
`## Method` and say which in `investigation.md`.

| family | Instruction | the question behind it | what a receipt is here |
|---|---|---|---|
| **audit** | `references/audit.md` | is this surface sound — coverage, performance, logic, features, bugs? | the surface you enumerated and what you sampled from it. "Nothing found" only means something beside the list of what was looked at |
| **refactoring** | `references/refactoring.md` | this code is hard to work in — what shape should it be? | what makes it hard, shown in the code, before any proposal; and what the new shape costs |
| **housekeeping** | `references/housekeeping.md` | what has gone stale — comments, dead code, unused declarations? | proof nothing reaches it. Unreferenced is not unused until you have looked past this repo's grep |
| **security** | `references/security.md` | what is exposed — risks, unsafe smells, unsanitized or junk data? | the path an attacker actually walks, named end to end. A worry is not a finding |
| **study** | `references/study.md` | how does someone else do this, and what should WE take? | the source pinned (commit, version, or URL + date read), and what transfers here kept separate from what they do |
| **deep-diagnosis** | `references/deep-diagnosis.md` | what is the mechanism behind a behaviour we cannot explain? | the narrowest located cause, what you ruled out on the way, and what you could not determine |

**Read your family's file above before you do anything else — it is your first tool call after this
skill loads, ahead of the brief, the code, and the scaffold.** It defines what counts as an answer
for this family and how to enumerate the surface; everything you read before it, you read without
knowing the bar. The kernel counts that read, and the review gate refuses an item whose investigate
never opened it.

Do not be misled by your own artifact looking correct without it: the scaffolder stamps the family's
sections from the template whether or not you ever read the method, so a record written blind comes
out the right SHAPE and the wrong DEPTH — measured 2026-08-13, five of nine investigations.

**A number is a receipt in every family** — the command, the repeats, the environment. Reading the
source gives you the complexity class, never the value.

Most items are mainly one family with some of another; you follow the one on the item. If the work
turns out to be a different family than triage judged, that is worth a line in your report — it is
the owner's to correct, and by then your record is already in the shape triage picked.

## Step 3 — Investigate with receipts

**Split the surface first, then read. The default is parallel Explore subagents (model: sonnet) —
one per question, or one per area when a single question spans several.** Each returns evidence with
pointers (`file:line`, a URL, a command's output), never summaries. You stay the synthesizer: a
subagent's finding is a lead until you have seen the receipt.

Independence is decidable, not a feeling — apply the test rather than weighing it:

> **Would knowing the answer to question B change HOW you read for question A?**
> No → independent → they go to separate subagents. Two questions over different files, different
> subsystems, or different sources are independent unless you can name the dependency.

Reading it all yourself is allowed exactly twice: when the answers genuinely chain (B tells you
where to look for A), or when the whole surface is small enough that splitting costs more than it
saves. **Either way you say so in `investigation.md`** — one line naming which questions chained, or
what the surface was and why it was too small to split. An unexplained single-threaded sweep reads
at the gate as a surface that was quietly narrowed, and the kernel counts your spawns whether or not
you mention them.

### Preflight, then split

Confirm the subject resolves and the surface is non-empty before you spawn anything: the path
exists, the repo is present, the URL answers. A subject that isn't there fails here, in one cheap
check — not six times over, once inside each parallel subagent, each billing you for the same
discovery.

### Every brief is self-contained

**A subagent inherits nothing.** It cannot see this skill, your family guide, or the item, so
whatever the brief does not carry, the work is done without. "Audit the auth module" buys you a
reader working to no bar — and its findings come back looking exactly like findings written to one.

Four things travel in every brief:

1. **The bar, pasted.** The lines of `references/<family>.md` that say what counts as a finding
   here — the text, copied in, not the path. Name the path as well so the subagent can reach the
   rest when it needs it.
2. **The boundaries**, from `## Boundaries`. The walls are yours to enforce and it cannot read them.
3. **The judgment it does not make.** Your guide's `## Fan-out` names what stays with you —
   severity, reachability, the shape, what transfers. Say so in the brief: a subagent that returns
   a verdict has answered a question nobody asked it.
4. **The return shape** — evidence with pointers (`file:line`, a URL, a command and its output).

**Done when** each brief still makes sense to someone who has read nothing else. The kernel records
what you sent; a brief too thin to carry a bar is visible at the gate as one.

- **When a question asks how something BEHAVES or how much it COSTS, measure it.** Reading the
  source tells you the complexity class, never the number. Throwaway scripts are fine, scoped into
  your own item folder: write the script there and run it as `cd <item-dir> && python3 bench.py`. An
  unscoped command at the repo cwd is denied — a research item is read-only on real code, and that
  holds for the shell too — so name no path outside the item folder and seed its fixtures there. A
  measurement that genuinely cannot be made is evidence when you say which number is missing and
  what it would have settled; a guess wearing a number's clothes is not.
- **Sources outside the repo are yours when the item or the owner names one.** `WebFetch` and
  `WebSearch` need no approval, so a link in the item's own description, in the brief's `## Context`, or in the
  owner's imported references is there to be opened. Pin it like a `file:line` — URL and the date
  you read it — and keep the distinction that matters: a doc page is evidence about the library,
  never about how THIS codebase uses it.
- **Material too big to keep is a wall, not a decision.** Your write boundary is the item folder, and
  it is not a place to park a cloned repository or a bulk dataset. If answering the question needs
  one, say so in `## Open threads` and in your report rather than doing it quietly — where that
  material should live is the owner's call to make, not yours to invent.
- **Don't go browsing past the Boundaries.** An open-ended search is answering beyond the walls with
  a browser.
- **`scaffold_artifact(item_id, "investigation")`, then keep it current as you go.** Its sections
  come from your family — fill the ones you are given. It is the record the report is written from,
  so a claim you cannot point at does not belong in it, and what did NOT pay off earns its section
  as much as what did: that is what stops the next investigation re-walking this one.


## Step 4 — Name the work this implies

**The findings are half of what this item owes; the work they imply is the other half.** A research
item exists so real work can follow it, and `## Follow-up work` is where that becomes possible —
items sized so someone could file them, in the order they should land, each naming what it touches.
Your family's guide says how to shape them.

Sizing is the judgment. "Improve error handling" is not an item; "validate the path argument in the
three handlers at `routes/fs.py:88,140,203`" is. Group findings that share one fix into one item.

**You record; you never file.** Every branch-off is decided in ONE place — the owner, at the review
gate, reading your report. Putting work on the board from here would commit them to it before they
chose it, which is exactly the step this section replaces.

## Step 5 — Write the user-facing report

Copy `templates/report-investigate-template.md` to `reports/report-investigate.md`, overwriting —
the owner's standing answer to "what do we know". Bank a `write_checkpoint` alongside it (which
questions are answered vs open, what the evidence leans toward, where to pick up).

- **`**Summary:**` is the finding in one line**, and the dashboard shows it alone: "every command
  re-reads the whole ledger, so the wait grows with the file". Not "the investigation is
  progressing".
- **`## How we know` separates measured from reasoned.** A number from a real run and a conclusion
  from reading must not read the same way — a guess in the clothes of a measurement is the one way a
  research item does damage.
- **`## What we didn't settle` is almost never empty.** Write it before you write the findings.

**Tone and style when writing to user-facing report**
- Plain, easy language. Fewer words wins.
- Never restate the item's kind, deliverable or id. Spend the space on the judgment behind them.
- Omit a prose field rather than filling it with "none" — an absent block reads better.

## Chat response style
- Use plain and easy language.
- Keep your response short, clear, and to the point.
- Do not use more than 30 words.
- Use bullets or numbered lists to organize information if there is more than one point.

## Reporting the run

`report_completion` says why THIS RUN stopped. **There is no loop here** — this run IS the phase, and
whatever you report, the item rests at the owner's gate afterwards. Nothing re-fires you on its own.

- **`success`** — every question is answered, or its Done criterion is met. Not "I found something
  interesting".
- **`partial`** — the honest ending for an investigation that ran out of run before it ran out of
  questions. Say which questions are still open; the checkpoint and `investigation.md` are what the
  next session picks up from.
- **`blocked`** — nothing could be investigated at all: the subject isn't reachable, or the
  boundaries make the work impossible.

**A run the kernel fired always declares an outcome**, and a run that skips it is recorded as
undeclared.

**Output style to report_completion.**
- Plain, concise, easy language. 
- Do not use more than 30 words.
- Keep your response short, clear, and to the point.

## Pitfalls

- **Editing code "just to try something"** — a research item is read-only on real code by contract;
  an experiment that must mutate it is a proposal, not a detour.
- **Answering beyond the boundaries** — thoroughness outside the walls is scope creep with a better
  reputation; park it as an open thread.
- **Recording a conclusion instead of what you saw** — this doc holds evidence; the verdicts are
  drawn later, from it.
