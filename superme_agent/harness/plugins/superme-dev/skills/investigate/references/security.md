# Security review — what is exposed

Read before a security investigation: the item asks what an attacker could reach, what handles
untrusted input carelessly, or what data is unsanitized, leaking, or junk.

## Contents

- **Two breadths** — boundaries are the unit, not directories
- **The bar: a path, end to end**
- **The surface: where trust changes hands**
- **What to look for** — by boundary
- **Data: sanitizing, leakage, junk**
- **What is defended** — why this section is mandatory here
- **Fan-out, and what not to run**
- **The follow-up is triage**
- **What a security review does NOT do**

## Two breadths

The item says whether this is **the whole repo** or **one area**. The bar — a path, end to end — is
the same at both.

| breadth | how you enumerate |
|---|---|
| **whole repo** | by BOUNDARY, one entry-point class at a time: routes, tool arguments, shell calls, deserialization, paths built from input, data read back out of storage. **Trust boundaries are the unit; directories are not** — a path starts at an entry point and ends wherever it ends, and a directory-shaped sweep cuts it in half |
| **one area** | every trust transition inside it — **including the ones whose far side is outside your area.** A boundary at the edge of the scope is still yours; walking it out is how you find that the caller validates nothing |

**Open `## Attack surface` with the breadth** — "whole repo, by boundary class" or the area named —
and its size.

## The bar: a path, end to end

A security finding is a story with an actor: *someone who controls X reaches Y and gets Z.* If you
cannot name all three, you have a worry, and a worry recorded as a finding is worse than one recorded
honestly — it burns the owner's attention and makes the real findings cheaper to ignore.

- **X** — what an attacker actually controls, and how they come to control it.
- **Y** — the code that mishandles it, at `file.py:214`.
- **Z** — what they get: data they shouldn't read, a write they shouldn't make, code execution, a
  denial, an identity they shouldn't have.

Severity follows the path, not the severity of the word: **high** = the path is live and complete;
**medium** = it needs a precondition you should name; **low** = defence in depth. A missing best
practice with no path is a hardening note, and it says so.

## Know the domain of the codebase <!--Complete this-->
- Understand the domain of working codebase and data characteristics (e.g., any sensitive data? - such as PHI, PII, and so on)
-  

## The surface: where trust changes hands

Enumerate before reading closely. Every place data crosses from less-trusted to more-trusted:

- Request handlers and their arguments; anything routed by user-supplied path or id.
- Tool and plugin boundaries — arguments an agent or a caller supplies.
- Shell invocation, and any string that becomes part of a command.
- Deserialization: JSON, YAML, pickle, config files, anything `eval`-adjacent.
- File paths built from input; anything that could traverse.
- Data read back OUT of storage and then trusted — the boundary people forget, because it was
  validated once, on the way in, under different rules.

Record the list with its size in `## Attack surface`.

## What to look for

| boundary | the question | the recurring miss |
|---|---|---|
| input → path / command / query | is it validated at every hop, or once at the edge? | trust that stops being checked one call inside the boundary |
| authn / authz | is the check on the DATA or on the route? | an object id that is validated but not owned by the caller |
| secrets | where does it come from, where does it end up? | a credential that is handled correctly and then logged |
| dependencies | what do we inherit? | judging a library by its README instead of its advisories |
| errors and logs | what does a failure reveal? | a stack trace or a verbatim query in a message the user sees |

## Data: sanitizing, leakage, junk

The item may be asking about the data itself rather than the code:

- **Unsanitized** — stored as given and rendered, executed or interpolated later. Name where it is
  stored and where it comes back out; the gap between those two is the finding.
- **Leaking** — data crossing a boundary it shouldn't: another user's scope, a log, a telemetry
  payload, an error message, a third party.
- **Junk** — malformed, orphaned or impossible records. Usually not an attack, usually a symptom;
  say what wrote it, or say you could not tell.

## What is defended

`## What is defended` is not optional in this family. "No exposures found" is unreadable without the
list of what was probed and what held — and the defence you name becomes the thing a later change
must not quietly remove. A security review with an empty defended section either found everything or
looked at nothing, and the reader cannot tell which.

## Fan-out, and what not to run

Split by BOUNDARY, one subagent per entry-point class, each returning candidate paths with
`file:line`. You walk each path yourself before it enters the record — a partial path is how a
non-finding gets written down as high.

**In the brief:** the X → Y → Z bar, pasted. With it a subagent returns paths, or says which leg it
could not complete — and both of those are useful. Without it, it returns smells.

**Read, reason, and probe within the item's own folder only.** Do not exercise a live system, do not
run an exploit against anything real, and do not craft input for a running service. A path you can
demonstrate on paper, with the code as evidence, is a finding; the demonstration is the owner's call
to authorize and not a detour inside a research run.

## The follow-up is triage

`## Follow-up work` is ordered by urgency, and it separates two things people conflate:

- **Fixes** — a live path exists. These are items with a real deadline, and the ordering is theirs.
- **Hardening** — no live path, but the defence is thin. Real work, different priority, and saying
  so is what keeps the fixes at the top.

Group by fix, not by finding: one missing validation reachable from six routes is one item.

## What a security review does NOT do

- **It does not patch.** Even the one-line fix. A research item cannot change code, and a quiet
  security patch is the change most likely to need a review.
- **It does not disclose.** Where a finding goes, and who is told, is the owner's decision.
- **It does not report worries as findings.** No path, no finding — a question instead.
