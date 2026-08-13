# Worktree commit style

Read before your first commit of a cycle.

```
Add a --category flag to tally

Only totals rows whose category matches; an unknown category is an
error rather than an empty report, so a typo stays visible.

SuperMe-Task: t3
```

- **Subject** — imperative, capitalized, no trailing period, ≤50 chars. Written for the PROJECT,
  whose readers have never heard of this workspace.
- **Body** — only when the subject alone would mislead. What and why; the diff shows how. Wrap at 72.
- **Trailer block** — git's `Key: value` form, one final block, no blank lines inside it.

## Examples

| good/bad | subject | why |
|---|---|---|
| ✅ | `Add a --category flag to tally` | names the change; a stranger can read it |
| ✅ | `Fix month rollover in the ledger export` | names what broke and where |
| ✅ | `Rename Ledger.total to Ledger.sum_rows` | one change, stated plainly |
| ❌ | `t3: add --category flag` | a task id in the subject means nothing to the repo — it belongs in the trailer |
| ❌ | `fix bug` | nothing findable later |
| ❌ | `Refactor ledger and clean up imports` | two changes in one commit; a failed check then points at both |
| ❌ | `fix c4: handle empty ledger` | `fix c4:` reads as a bugfix to anyone outside SuperMe — put `SuperMe-Check: c4` in the trailer |

## Trailers

- **`SuperMe-Task: t<n>` on every commit**, at the `- [x]` tick. One commit per task.
- Checkpoint commits between tasks are cheap and welcome — mark them `SuperMe-Task: t<n> (wip)`.
- A fix answering a failed check adds `SuperMe-Check: c4` beside the task trailer.

The task trailer is the one rule a hook enforces: a commit on an item branch without it is
rejected, because the diff walkthrough cannot be reconstructed afterwards. A rejection about
anything ELSE came from this project's own checks — park and ask, never retry, never `--no-verify`
(SKILL.md step 2).

These commits are squashed at the merge and never reach the project's permanent history. That is
precisely why they still have to read as plain git: their readers — the vetter, the review report,
the diff walkthrough — are people.
