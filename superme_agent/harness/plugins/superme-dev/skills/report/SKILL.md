---
name: report
description: "Write a research work-item's findings.md: distill the investigation into grounded findings, implications, and follow-ups. Use when a research-kind work-item is in its report phase after investigation; not for doing the investigation itself (use investigate) or for implementation closeouts (use close)."
argument-hint: "work-item id (optional — defaults to the bound item)"
category: workspace
---

# Report (research item)

Distill the investigation into the artifact that outlives it. The findings doc is what the owner
reads and what future items cite — the notes were for you, this is for them.

## 1 — Draft findings.md

`scaffold_artifact(item_id, "findings")`, then fill:

- **Questions** — each of the plan's questions, restated with its one-line answer.
- **Findings** — the substance: each finding with its evidence pointer (`file:line`, source,
  measurement) from your investigation notes. A finding states what IS, not what you suspect —
  mark genuine uncertainty explicitly rather than rounding it to confidence either way.
- **Implications** — what the findings mean for THIS project's decisions: what they unblock,
  contradict, or recommend.
- **Follow-ups** — what emerged that is out of this item's scope.

Write it self-contained: readable without the notes, pointers where depth matters.

## 2 — Follow-ups become items

Each follow-up worth acting on → `create_inbox_item` (relation `spawn`, context in the brief) and
name it in the Follow-ups section. A follow-up that lives only in prose is lost by next month.

## 3 — Hand over

Tell the owner findings are drafted — lead with the one-sentence upshot, then the per-question
verdicts. The owner advances to close; research items merge nothing.

## Pitfalls

- **Findings without pointers** — an unevidenced finding can't be trusted or re-verified;
  it dies with the transcript.
- **Re-running the investigation while writing** — report distills what was found; a hole found
  while writing goes back to the owner as "one more investigate pass", not a silent re-opening.
