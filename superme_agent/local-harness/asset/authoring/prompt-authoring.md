---
name: prompt-authoring
description: The bar for a prompt written in code rather than on disk - its three delivery layers, what each one costs, and how to write for a reader who never asked.
enabled: true
hub-only: true
---

# Best practice for writing in-code prompts

An in-code prompt is text the engine puts in front of an agent without the agent asking: the system
block it carries every turn, the message that opens a background run, the sentence a refused tool
returns. Covers all three. Does not cover skills, tools, or files an agent chooses to open.

## The three layers

Every prompt is exactly one of these. They are paid differently, so they are written differently.

| layer | arrives | paid | its job |
|---|---|---|---|
| **system** | every turn, in the system block | length × turns, re-sent on every step | the standing frame: where the agent is, what bounds it |
| **trigger** | once, as the opening message of a run | re-sent on every step of that run | the delta the agent cannot read off disk |
| **interjection** | at the moment of a refusal or a failure | once | correct the next action |

- **Declare the layer where the prompt is defined.** A reader who cannot tell which layer a string
  is in cannot tell what its length costs, and edits it to the wrong bar.

## The bar — every layer

- **Prompts are for agents (LLMs), not humans.**

- **Every line instructs the next action.** Test: after reading it, what does the agent do
  differently? Nothing → cut it.

- **Instruct; do not describe.** No system narration, no background, no rationale for the design.
  How the engine fires the run and what a gate counts change nothing the agent does.

- **Do not state the obvious.** If a competent agent would do it anyway, cut it.

- **Give a reason only where it changes the action** — a clause, never a paragraph.

- **Prefer a checkable test to an exhortation.** "Be careful", "be thorough", "use good judgment"
  produce no change in behaviour. Give a criterion with a yes/no answer.

- **Say it once, in one layer.** Pick the layer whose timing matches the rule: a boundary the agent
  holds all run belongs in the system layer; one it meets only when it crosses belongs in the
  interjection. A rule in two layers drifts in one, and the reader cannot tell which is current.

- **Obey every rule you state.** Style is demonstrated, never asserted — the reader pattern-matches
  the text in front of it before it parses the instruction.

- **Name the exact string.** Paths, tool names, commands, identifiers — verbatim, absolute where the
  working directory is not guaranteed, and only if it exists.

- **Dense, not terse.** Cut words, keep rules. Removing a sentence must lose something the agent
  would have done differently.

- **Write concise, clean, direct prompts.** One idea per sentence. No hedging, no throat-clearing,
  no stacked intensifiers.

## System layer — the standing frame

Scanned, not read. The same text on turn forty as on turn one.

- **Write a lookup table, not prose.** Labelled lines, one fact each.

- **Keep it stable.** The system block is cached across turns; a value that changes every turn
  invalidates the cache for everything after it. Volatile facts belong in the trigger, or last.

- **Budget it against a measurement**, never an estimate.

- **Factor the branches.** Where variants of the frame restate a shared half, state it once, then
  state only what differs.

**Bad example**
```
You are currently operating within the deployment subsystem. It is important to understand
that this subsystem manages releases across several environments, and that the release
process has been designed carefully over time to ensure safety and reliability. You should
always be careful when making changes here, and you should keep in mind that other teams
depend on this system working correctly.
```

**Good example**
```
## Current focus
- release: **r-4471** — "checkout latency"
- stage: `canary`
- config: `/srv/releases/r-4471/`

**This stage:** hold the canary at 5% and read its error rate — procedure in the
`canary-watch` skill.
**Edit boundary:** `/srv/releases/r-4471/` only. Writes elsewhere are denied.
```

## Trigger — the opening message of a run

One message at the start, re-sent for the rest of the run.

- **Carry the delta, nothing else.** What the agent cannot obtain by reading. A trigger that
  restates a file is paid on every step, and the file is still there.

- **Name the procedure; do not inline it.** Which skill, on which subject.

- **State what changed since the agent last looked**, when it has looked before. An agent resuming a
  thread trusts its memory of a file, and that memory describes the previous version.

- **Assume the run gets compacted.** Anything load-bearing must also exist somewhere
  the agent can re-read.

**Bad example**
```
Run release:canary-watch for release `r-4471`. The plan for this release says to hold at 5%
for 30 minutes, then step to 25% if the error rate stays under 0.5%, then to 50%, then to
100%. The error budget for this service is 0.1% monthly. The rollback command is
`relctl rollback r-4471`. The service owner is the payments team...
```

**Good example**
```
Run release:canary-watch for release `r-4471` ("checkout latency").

You have watched this canary before and this is the same thread, but the plan was rewritten
since your last pass. Re-read `/srv/releases/r-4471/plan.md` before you judge anything —
what you remember of it describes the previous version.
```

## Interjection — the correction

Arrives at a known failure, addressed to an agent that has just made a specific wrong move.

- **Length is not the constraint here.** Paid once, at the exact moment of confusion. Cut nothing
  that changes the next action.

- **Name the wrong move.** A generic refusal sends the agent hunting for a rule it must now infer.

- **Give the way through in the same message**, as something it can run.

- **Say who refused — or say plainly that nobody did.** An unanswered request and a denied one look
  identical from the inside, and an agent that guesses wrong invents a blocking rule.

- **Close the routes you do not want taken.** Retrying, reshaping the same call, and quietly
  choosing a different target are the three defaults. Name the ones that apply.

**Bad example**
```
Permission denied. This operation is not allowed.
```

**Good example**
```
That command writes outside this run's boundary, `/srv/releases/r-4471/` — the shell did not
start there and the command does not name it. Name it and it runs with no approval:
`cd /srv/releases/r-4471 && <your command>`, or `git -C /srv/releases/r-4471 …`. Do not retry
it unchanged and do not write somewhere else instead.
```

## A string with more than one reader

- **Write for one reader, derive the other.** A sentence rendered into both an agent's prompt and a
  person's screen serves neither: the agent needs the next action, the person needs to know what is
  wrong. A string that reads as a good status line is a bad instruction.
