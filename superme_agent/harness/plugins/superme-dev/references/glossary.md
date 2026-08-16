# The workspace vocabulary

Every skill in this plugin, every artifact template, and every report writes in these words. Use
them exactly. A synonym is not a style choice here — two names for one thing is how a router ends up
reading the wrong field, and how two phases describe the same act as if it were two.

Read this when you are writing a skill, a template, an artifact, or a report. Read the `Avoid` line
as hard as the definition: it names the word that will be understood as something else.

## The unit of work

**Work-item** — one unit of this host's dev work, with its own folder, its own phases, and its own
gates. The only container real dev work runs in.
*Avoid*: task (that is a line inside a plan), ticket, story, issue.

**Kind** — `implementation` (changes the codebase) or `research` (answers a question, changes no
code). The kind decides which phases exist, so it is not a label — it is the shape of the pipeline.
*Avoid*: type, category.

**Family** — which investigation a research item is: `audit · refactoring · housekeeping · security ·
study · deep-diagnosis`. The family decides what counts as an answer and which guide sets the bar.
*Avoid*: research type, sub-kind, mode.

**Item folder** — the work-item's own directory: `artifacts/`, `reports/`, `checkpoints/`, and
whatever experiments it needed. For a research item this is the entire write boundary.
*Avoid*: workspace (that is the whole dev surface), scratch.

**Worktree** — the git checkout an implementation item builds in, on its own branch. Research items
have none, and that absence is what makes a research item read-only on real code.
*Avoid*: sandbox, checkout, branch (the branch is a name; the worktree is a place).

## The machinery of a phase

**Phase** — where the item is in its pipeline: `triage · plan · build · vet · review · investigate ·
close`. A property of the ITEM.
*Avoid*: stage, step, state.

**Gate** — the pause at the end of a phase where a decision is made, by the owner or the deputy. A
phase runs; a gate decides. Gates carry **checks**, and a check is either blocking (it greys Approve)
or visible (it informs the decision and nothing else).
*Avoid*: approval, checkpoint (that word is taken — see below), review (that is a phase).

**Run** — one execution of an agent against the item: it starts, does work, and declares an
**outcome**. A phase can take several runs.
*Avoid*: job, invocation, turn (a turn is one exchange inside a session).

**Session** — the conversation thread a run happens in. **A session belongs to a PHASE**: entering
the same phase again resumes its thread, moving to a different phase mints a fresh one. So a session
outlives a run, and a run is never a session.
*Avoid*: thread as a formal term (fine in prose), context, chat.

**Cycle** — one build⟷vet round on an implementation item, recorded in `artifacts/build-vet-<n>.md`.
Cycles are numbered and they accumulate; the **arc** across them is what review reports.
*Avoid*: iteration, loop (the loop is the machinery, a cycle is one pass through it).

**Checkpoint** — a `write_checkpoint` bank in `checkpoints/`: where the work stands, so the next
session can pick it up. Not a gate, not an approval.
*Avoid*: save, snapshot, milestone.

**Outcome** — why a run stopped, declared by the agent through `report_completion`:
`success · clean_noop · blocked · approval_required · exhausted · stagnated`, plus `partial` where
the phase supports stopping mid-work. An outcome describes the RUN, never the item's fate.
*Avoid*: status (that is the item's — `active`, `awaiting_human`, `error`, …), result, verdict.

## The two documents

The split is enforced everywhere and it is the one most easily blurred:

| | **record** (`artifacts/*.md`) | **report** (`reports/report-*.md`) |
|---|---|---|
| written for | the next phase, and the phases after it | the owner, at the gate |
| holds | what was done and seen, with pointers | what to decide, and how much to trust it |
| on a rewrite | the shape its template gives it | overwritten whole — always the item as it stands now |

**Record** — the agent-facing artifact. Evidence, not verdicts. A claim it cannot point at does not
belong in it.
*Avoid*: doc, output, deliverable.

**Report** — the user-facing document. An INTERPRETATION of the record, not a shorter copy of it.
*Avoid*: summary, digest — both invite the shortening this document exists to prevent.

**Anchor doc** — the project's standing knowledge in `general/`: `project-prd · architecture ·
capabilities · decisions · roadmap · verification` (+ `resources/`). They describe the PROJECT, never
one item.
*Avoid*: docs, spec, README.

## The words for proof

**Receipt** — what makes a claim checkable by someone who was not there: `file:line`, a command and
its output, a URL with the date read, a number with the recipe that produced it.
*Avoid*: proof, source, citation.

**Claim** — a statement that has not yet shown its receipt. Not an insult — half of good work is
claims on their way to becoming findings.
*Avoid*: assertion, hypothesis (that word means something narrower in `deep-diagnosis`).

**Evidence** — what was observed. Distinct from the **conclusion** drawn from it, and the two live in
different sections on purpose.
*Avoid*: data, findings (a finding is a conclusion with a receipt attached).

**Check** — one row of the vet plan: a `scenario` to run, an `expect` to match, and a `proves:` line
saying what a green is supposed to MEAN. A check that passes without proving its `proves:` is a check
pointed at the wrong thing.
*Avoid*: test (a test is code; a check may be a person's eye), validation, assertion.

**Bar** — what counts as good enough here, stated so it can be failed. Every family guide is mostly
a bar; a bar nobody can fail is a mood.
*Avoid*: standard (reserved — see `decisions.md`, the project's recorded standards), criteria, quality.

## The words for scope

**Surface** — everything a sweep or an audit could look at, enumerated BEFORE any of it is read
closely. Its size is what makes every later number readable.
*Avoid*: scope, area (an area is one slice of a surface), codebase.

**Boundary** — a wall the work must not cross, from the plan's `## Boundaries` or the item's write
permissions. A thread leading outside is recorded, never chased.
*Avoid*: limit, constraint, seam (a seam is a place to substitute behaviour, not a wall).

**Write boundary** — the paths a phase may write to. For research: the item folder, and nothing else,
including from the shell.

## Who acts

**Owner** — the human. Decides at gates, and is the only one who can ratify a departure from a
recorded decision.
*Avoid*: user (used for the PROJECT's end users), you, the human.

**Deputy** — the delegated judge that can approve at a gate when the owner is away, from the same
checks the owner is shown. It judges; it never does the work it judges.
*Avoid*: reviewer, approver, bot.

**Subagent** — an isolated worker spawned for one scoped job, which **inherits nothing**: whatever
its **brief** does not carry, the work is done without. It returns observations; the judgment stays
with whoever spawned it.
*Avoid*: agent (ambiguous with the phase agent), worker, task.

**Brief** — two different things by context, and both are real: the subagent's prompt (above), and
`artifacts/brief.md`, triage's record for the plan phase. Say "subagent brief" or "the item's brief"
wherever the sentence could go either way.

## Rejected framings

What these words will NOT mean here, and why — so the same argument is not had twice.

- **"Validate" for what vet does.** Renamed to **vet** deliberately. Validation checks a thing
  against its own spec, which is exactly the self-grading the phase exists to prevent; vetting is
  adversarial and belongs to someone who did not build it.
- **"Estimated" anything in a usage number.** A usage column means MEASURED. Unmeasured is 0, never
  an estimate and never a different metric wearing this one's name.
- **Assumptions as a recorded artifact.** `assumptions.md` existed and was demolished. An assumption
  nobody ratifies is a decision made quietly; the honest forms are a question to the owner or a
  decision recorded with its cost of being wrong.
- **"Sweep memory" — a sweep inheriting the last sweep's judgment.** Rejected: inheriting a judgment
  means inheriting a stale one. Sweeps start fresh, deliberately.
- **`diagnosis` as a family name.** The family is `deep-diagnosis`, because a `diagnosis` SESSION
  already exists — a quick read of one run's trace. Two meanings on one token is how wrong-field bugs
  start, which is not hypothetical here (see below).
- **`measurement` as a family.** Performance lives inside `audit`. A number without a re-runnable
  recipe is a bad receipt in every family, not a family of its own.

## Flagged ambiguities

Pairs that read as synonyms and are not. Each one has already cost something.

- **`feature` vs `phase` on a run row.** They coincide for a background phase run
  (`feature=phase=review`) and DIVERGE for an owner talking to that phase (`feature='chat'`,
  `phase='review'`). A router that reads one where it means the other misses silently. **Cost:** the
  owner's only route back from a gate was dead for weeks, under a comment describing the correct
  behaviour.
- **`revise` vs "send-back".** One act, two names — `revise` in prose and at the gate, `send_back`
  in the code. There is no send-back BUTTON anywhere; the conversation is the context. Prefer
  **revise** in anything an agent or the owner reads.
- **Slot vs role, for sessions.** A **slot** answers "which thread" (one per phase); a **role**
  answers "what sort of thread" (`intake · build · vet`, the spine's `session.kind`). They are
  deliberately not the same list.
- **Run vs session.** A session holds many runs; a run belongs to one session. "The agent" is
  neither — say which.
- **Check vs verification.** A **check** is the row in the plan; the `## Verification` fence is the
  machine-written record of what running it produced. Only `record_verification` writes the fence.
- **Sweep — two live meanings.** A **standing sweep** is a research work-item over the codebase
  (`audit · refactoring · housekeeping · security`), launched from the workspace bar. A **capture
  sweep** mines a conversation slice for durable learnings and files candidates. Nothing connects
  them but the word. Say which, and note that the routes are deliberately far apart:
  `/dev/research/sweeps` versus `/dev/sweep`.
