# Revising a plan

Read this when the item arrived here from review carrying feedback and an existing `plan.md` that
build already worked against. A first plan never needs it.

## Contents

- The one way in
- Split the feedback into concerns
- What the tool's fields cannot tell you
- What you do not decide
- Where this rejoins the skill

## The one way in

Change the plan **only** through `revise_plan` — never by rewriting the file. The tool validates the
whole revision before it writes anything, appends the `## Revision r<n>` block build reads, and
leaves `## Tasks` and `## Verification plan` structurally last so nothing above them can contradict
them.

## Split the feedback into concerns

One review conversation usually carries several: the loop hit its budget AND two checks failed AND
the caching approach was wrong. Each becomes one entry in `changes`, with its **own** scope — so
redesigning one part never resets the progress another part earned.

The tool's `scope` field defines the three (`resume` · `targeted` · `redesign`) and states what each
requires; `area`, `note`, `directive`, `still_in_force` and `superseded` each say what they carry.
Read them there rather than here — they are in front of you whenever you call it.

## What the tool's fields cannot tell you

- **A `targeted` change edits `## Tasks` task by task** — `add_task` / `remove_task`, never a
  section rewrite. A ticked checkbox is progress build earned, and the owner watches those boxes.
- **The proportionality rule is a refusal, not advice.** If a concern needs no plan change, its
  scope is `resume`. Do not manufacture an edit to have something to show, and do not re-instruct
  build on parts nobody complained about. Over-modification is the failure this grammar exists to
  prevent, and `resume` is the honest answer to *"looks close, try more"*.
- **A `redesign` removes its dead tasks EXPLICITLY.** Nothing resets for you, because a guess is
  worse than the exact list. Name what is void in `superseded` and say what build must undo —
  forward, with new commits that revert, never a reset or a force-push.

## What you do not decide

The concern types and the budget are not yours to tag: code reads those off the loop's exit and the
authorization ledger. Your revision opens a fresh build⟷vet generation.

## Where this rejoins the skill

Step 5. The report is written from the revised plan, not from the feedback.
