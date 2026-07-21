# Authoring contract — `general/capabilities.md`

**What it is.** What the system can do **right now**, in the user's words. The answer to "what does
this thing actually do today" — asked by the owner, by a new contributor, and by you at the start of
any work that might duplicate something already shipped.
**Write / update.** Empty at project-init (nothing has shipped — say so). Reconstructed from the code
at retrofit. Then **one line is added when a deliverable closes**, and only then.
**Length.** One line per capability, ≤100 lines. If it needs a paragraph, the paragraph belongs in
`architecture.md`.

## The one rule that makes this doc worth having
**Present tense only.** A capability appears here when it works, never when it's planned, in progress,
or nearly done. The moment this file mixes shipped with intended, it stops answering its question and
becomes a second roadmap — and a reader who can't trust it will stop reading it.

The check: could you demonstrate this line, right now, on a clean checkout? If no, it doesn't belong.

## Sections
| # | Section | Holds |
|---|---------|-------|
| 1 | `## Capabilities` | One `- **<name>** — <what the user gets> (<trigger>)` per shipped capability. |

Group under `### <area>` sub-headings only once past ~15 entries. Until then a flat list reads faster.

## Per-line contract
`- **<capability name>** — <what the user can now do> (<how they invoke it>)`

- **Name** — what the user would call it, not what the code calls it. `UserManagementTable` is a
  component; "User list" is a capability.
- **What they get** — the outcome, in their language. Not the mechanism.
- **Trigger** — the command, flag, route, or button that reaches it, inline in parens. This is what
  makes the doc a dispatch table rather than a brag list.

Add ` — see D-NNN` only when a capability's *shape* is the direct result of a decision worth recalling.

## Rules
- **Shipped only** — see above. It's the whole contract.
- **Never fabricate.** If you can't tell from the code whether something works, don't list it. At
  retrofit, mark genuine uncertainty `[TBC — <what you observed and why you're unsure>]` rather than
  guessing; an unverified claim here is worse than a missing one.
- **One owner per fact** — *what it will deliver* is `project-prd.md`, *what's coming* is
  `roadmap.md`, *how it's built* is `architecture.md`. This doc holds only the present tense.
- **Empty is a valid state.** A greenfield project's capabilities file says so in one line. Do not
  pad it with the plan.
- **Removal is part of the contract.** A capability that gets removed comes out of this file in the
  same change. This doc is replace-only, not a log.
- **No frontmatter.**

## Template
```markdown
# <Project> — capabilities

What <project> can do right now. Present tense only: an entry lands when the deliverable carrying it
closes, never when it's planned.

## Capabilities
- **<name>** — <what the user gets> (`<trigger>`)
```
