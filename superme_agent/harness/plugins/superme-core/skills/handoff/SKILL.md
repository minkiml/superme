---
name: handoff
description: Compact this conversation into a handoff document a fresh agent can start from. Use when the user asks to hand off, wrap up, or write a handoff for the next session. Not for a work-item's continuity checkpoint (the kernel fires that itself), and not for summarizing a document.
argument-hint: "What will the next session be used for?"
category: general
---

# Handoff

Write the part of this conversation that exists nowhere else, so the next agent is not guessing.

## Steps

1. **Scope it.** An argument names what the next session is for — tailor the doc to it. No
   argument means cover the whole conversation.
2. **Write these five sections, in this order:** **Goal** · **State** (done, in progress, blocked)
   · **Key decisions** (each with its why) · **Next steps** (concrete, ordered) · **Suggested
   skills** (which skills the next agent should invoke).
3. **Point at what is already written; never copy it.** A plan, a diff, a ticket, a commit goes in
   as a path or a URL. Restating it creates a second version that drifts from the first.
4. **Lead `State` with the user's last unmet ask, in their own words.** Quote it. An unanswered
   question counts as outstanding.
5. **Mark what is dead.** An approach that was ruled out, a task that was cancelled — say so, or
   the next session finishes it out of momentum.
6. **Separate what you checked from what you believe.** "Tests pass" will be acted on without
   re-checking: cite the command or report that proves it, or say it is unverified.
7. **Redact secrets** — write `[REDACTED]` for any key, token, password, or personal data.
8. **Save** it as `handoff-<slug>.md` in the OS temp directory — `$TMPDIR` on macOS and Linux,
   `%TEMP%` on Windows — never in the workspace. Report the absolute path.

## Pitfalls

- **Restating the artifacts** — if a file already says it, the handoff says where the file is.
- **A next step nobody can act on** — "continue the work" names no file and no command.
- **Saving into the repo** — a handoff is scratch, and a committed one goes stale immediately.
