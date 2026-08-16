# Security review — what is exposed

The item asks what an attacker could reach, what handles untrusted input carelessly, or what data is
unsanitized, leaking, or junk.

## Contents

- **The bar** — a path, end to end
- **Know the domain first**
- **Pick your breadth** — boundaries are the unit, not directories
- **Enumerate where trust changes hands**
- **What to look for**, by boundary
- **Data: unsanitized, leaking, junk**
- **What is defended** — mandatory in this family
- **Splitting the work, and what not to run**
- **Shaping the follow-up** — it is triage
- **What a security review does NOT do**

## The bar

A finding is a story with an actor: **someone who controls X reaches Y and gets Z.** If you cannot
name all three, you have a worry — record it as one, in those words.

- **X** — what an attacker actually controls, and how they come to control it.
- **Y** — the code that mishandles it, at `file.py:214`.
- **Z** — what they get: data they shouldn't read, a write they shouldn't make, code execution, a
  denial, an identity they shouldn't have.

Severity follows the path, not the alarm in the word: **high** = the path is live and complete;
**medium** = it needs a precondition, and you name it; **low** = defence in depth. A missing best
practice with no path is a hardening note, and it says so.

**Bad and good examples**
```example
✗ "`run_report()` builds a shell command by string concatenation — command injection risk."
✓ "The `name` field of POST /reports reaches `run_report()` at jobs.py:88, which interpolates it
   into a shell string; a caller who can create a report runs arbitrary commands as the daemon
   user. No validation between the handler and the call — checked all three hops."
```

## Know the domain first

Before you enumerate anything, establish what this codebase handles and what would be costly to
lose: which data classes it stores (personal data, health data, credentials, payment details,
customer content), where they live, and who is supposed to reach them. Severity is meaningless
without it — the same leak is `low` for public data and `high` for a medical record.

Write what you established, and where you got it. If the codebase does not say, that is itself worth
recording.

## Pick your breadth

| breadth | how you enumerate |
|---|---|
| **whole repo** | by BOUNDARY, one entry-point class at a time: routes, tool arguments, shell calls, deserialization, paths built from input, data read back out of storage. **Trust boundaries are the unit; directories are not** — a path starts at an entry point and ends wherever it ends, and a directory-shaped sweep cuts it in half |
| **one area** | every trust transition inside it, **including the ones whose far side is outside your area**. A boundary at the edge of the scope is still yours; walking it out is how you find that the caller validates nothing |

State the breadth in the first line of `## Attack surface`, with its size.

## Enumerate where trust changes hands

Before reading closely, list every place data crosses from less-trusted to more-trusted:

- Request handlers and their arguments; anything routed by user-supplied path or id.
- Tool and plugin boundaries — arguments an agent or a caller supplies.
- Shell invocation, and any string that becomes part of a command.
- Deserialization: JSON, YAML, pickle, config files, anything `eval`-adjacent.
- File paths built from input; anything that could traverse.
- Data read back OUT of storage and then trusted — the boundary people forget, because it was
  validated once, on the way in, under different rules.

## What to look for

| boundary | the question | the recurring miss |
|---|---|---|
| input → path / command / query | is it validated at every hop, or once at the edge? | trust that stops being checked one call inside the boundary |
| authn / authz | is the check on the DATA or on the route? | an object id that is validated but not owned by the caller |
| secrets | where does it come from, where does it end up? | a credential handled correctly and then logged |
| dependencies | what do we inherit? | judging a library by its README instead of its advisories |
| errors and logs | what does a failure reveal? | a stack trace or a verbatim query in a message the user sees |

## Data: unsanitized, leaking, junk

The item may be asking about the data itself rather than the code:

- **Unsanitized** — stored as given and rendered, executed or interpolated later. Name where it is
  stored and where it comes back out; the gap between those two is the finding.
- **Leaking** — data crossing a boundary it shouldn't: another user's scope, a log, a telemetry
  payload, an error message, a third party.
- **Junk** — malformed, orphaned or impossible records. Usually a symptom, not an attack; say what
  wrote it, or say you could not tell.

## What is defended

This section is not optional here. "No exposures found" is unreadable without the list of what was
probed and what held — and each defence you name becomes something a later change must not quietly
remove. An empty defended section means the review either found everything or looked at nothing, and
the reader cannot tell which.

## Splitting the work, and what not to run

Split by BOUNDARY — one reader per entry-point class, each returning candidate paths with
`file:line`.

Paste the X → Y → Z bar into every brief. With it a reader returns paths, or says which leg it could
not complete, and both are useful. Without it, it returns smells.

**You walk each path yourself** before it enters the record. A half-walked path is how a non-finding
gets written down as high.

**Read, reason, and probe inside the item's own folder only.** Do not exercise a live system, do not
run an exploit against anything real, and do not craft input for a running service. A path you can
demonstrate on paper, with the code as evidence, is a finding; running it is the owner's call to
authorize.

## Shaping the follow-up

Order by urgency, and separate the two things people conflate:

- **Fixes** — a live path exists. Real deadline, and the ordering is theirs.
- **Hardening** — no live path, but the defence is thin. Real work, different priority; saying so is
  what keeps the fixes at the top.

Group by fix, not by finding: one missing validation reachable from six routes is one item.

## What a security review does NOT do

- **It does not patch.** Even the one-line fix. A quiet security patch is the change most likely to
  need a review.
- **It does not disclose.** Where a finding goes, and who is told, is the owner's decision.
- **It does not report worries as findings.** No path, no finding — a question instead.
