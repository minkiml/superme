# Worktree commit style

Read before your first commit of a cycle.

Every commit splits in two, and that split is the whole rule:

```
Add a --category flag to tally

Only totals rows whose category matches; an unknown category is an
error rather than an empty report, so a typo stays visible.

SuperMe-Task: t3
```

- **Above the blank line is for the project.** Imperative mood, capitalized, no trailing period,
  at most 50 characters. Whoever reads this repository's history has never heard of this
  workspace — no task numbers, no item ids, no phase names in the subject or body.
- **The trailer block is for SuperMe.** `SuperMe-Task: t<n>` joins the commit to the plan's
  `## Tasks` line, which is what lets the review page walk the diff task by task. Git's own
  `Key: value` form, one final block, no blank lines inside it.
- **A body only when the subject alone would mislead** — a line or two on the non-obvious choice.
  What and why; the diff already shows how. Wrap it at 72 columns.

Following from that:

- **One commit per task**, at the `- [x]` tick. Never batch a cycle into one commit — a failed
  check then points at everything.
- Checkpoint commits between tasks are cheap and welcome; mark them `SuperMe-Task: t<n> (wip)`.
- A fix answering a failed verification names the check in the trailers — `SuperMe-Check: c4` —
  not in the subject. `fix c4:` in the subject would read as a bugfix to anyone outside SuperMe.

The trailer is the one rule a hook enforces: a commit on an item branch without
`SuperMe-Task: t<n>` is rejected, because the diff walkthrough cannot be reconstructed afterwards
from a commit that never carried its task. Everything else here is style, and style is yours to
get right. If a rejection is NOT about the trailer, it came from this project's own checks — see
the skill's Step 2: park and ask, never retry, never `--no-verify`.

These commits are squashed at the merge and never reach the project's permanent history. That is
precisely why they still have to read as plain git: their readers — the vetter, the review report,
the diff walkthrough — are people.
