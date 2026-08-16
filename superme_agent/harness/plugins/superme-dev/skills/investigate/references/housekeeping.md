# Housekeeping — what has gone stale

Read before a housekeeping sweep: the item asks what should not be here any more — comments that
describe code that changed, dead functions, unused variables and declarations, abandoned config,
anything that looks suspicious or unaccounted for.

The question is not "is this good code". It is **"does anything still reach this"**, and that is a
question with a provable answer.

## Contents

- **Two breadths** — sweep by kind, or sweep an area exhaustively
- **Start mechanical** — the declaration inventory that produces your shortlist
- **The bar: proof, not absence of evidence**
- **What to sweep** — the four kinds of stale
- **What looks dead and isn't** — the section that pays for itself
- **Suspicious ≠ malicious**
- **Fan-out**
- **The follow-up is the deliverable**
- **What housekeeping does NOT do**

## Two breadths

The item says whether this is **the whole repo** or **one area**. Same bar, different sweep.

| breadth | how you sweep |
|---|---|
| **whole repo** | by KIND — one of the four kinds below at a time, all the way across the tree, then the next. Directory-by-directory looks tidier and is worse: the fourth kind gets attention in the first two directories and is quietly dropped everywhere after |
| **one area** | all four kinds inside it, exhaustively. This is the breadth where a deletion list can be complete, and the record should say so |

**Open `## Surface & sample` with the breadth** — "whole repo, by kind" or the area named — and the
sizes. A later sweep starts from that line.

## Start mechanical

**Dead code is found by counting, not by reading.** Before any judgment, produce the shortlist
mechanically — it costs one pass over the tree and it is the only way to cover every declaration
rather than the ones you happened to look at.

1. **List the declarations.** One grep for definition lines across the tree, names only.
2. **Count each name's occurrences** repo-wide, across code AND config, docs, templates and scripts —
   the places a string-reached caller hides.
3. **A name occurring once is a candidate**: the only mention is its own definition.
4. **A name whose other mentions are all in its own file is also a candidate** — reachable inside a
   module that nothing outside reaches is the same as unreachable.

```example
✓ grep -rhoE "^(def|class) [A-Za-z_]+" --include="*.py" . | awk '{print $2}' | sort -u > syms
  then, per name: grep -rowE "\b<name>\b" . | wc -l   → keep the ones at 1
```

Use the equivalent for whatever the tree is written in — exported functions, classes, components,
config keys. **Report the two numbers**: how many declarations were inventoried, and how many
survived to the shortlist. That pair is your coverage receipt for this kind, and it is checkable.

The shortlist is where the judgment starts, not where it ends: the section below applies to the
shortlist, not to the tree. Inverting that order is what makes a sweep expensive and incomplete at
the same time — reading files to find candidates, then having no budget left to prove them.

## The bar: proof, not absence of evidence

`grep` returning nothing is where this work STARTS, not where it ends. Before anything is called
dead, check the ways a caller can hide from a text search:

- **Reached by string** — a name in config, a route table, a plugin registry, a task or agent name,
  a template.
- **Reached from outside this repo** — a public API, a CLI entry point, an import by a sibling
  project, a script someone runs by hand.
- **Reached by convention** — a hook, an override, a fixture the test runner collects by naming
  rule, a subclass method the base class calls.
- **Reached indirectly** — reflection, dynamic dispatch, a decorator that registers on import.

Each item in `## What can go` carries how you searched and which of these you ruled out. Anything
you cannot prove either way is an OPEN THREAD, never a deletion — this family's one catastrophic
failure is removing something that was reachable, and it is always cheaper to leave it.

**Follow the graph, not the reference count.** A group of files that only reference each other is
not reached — it is a dead island with internal traffic, and a naive count reads it as busy. When
something looks live because many files reference it, ask what reaches THOSE files, and keep
walking outward until you arrive at something the system actually starts: an entry point genuinely
invoked, a route genuinely served, a command a person or a script genuinely runs.

```example
✗ "The legacy CLI package is live — 30-odd files reference it, and it has its own entry point."
✓ "Every one of those references is inside the package. Nothing invokes the entry point: no
   script, no CI job, no docs. The package is dead as a unit — except its config module, which
   the live server imports directly."
```

This is the family's other catastrophic failure, and it is the expensive one: leaving a whole
retired subsystem in place because its internal traffic looked like life. **Where a group dies as a
unit, propose it as one item** and name the members that survive it.

**A clean area is a claim too.** "Swept, nothing found" needs the same receipt as a finding — the
surface enumerated and how it was searched. Without that it is indistinguishable from an area
nobody read, and it is worse, because it retires the question. An area handed to you clean but
without its enumeration is UNSWEPT; record it as an open thread.

## What to sweep

| kind | what makes it stale | the recurring miss |
|---|---|---|
| comments and docstrings | it describes behaviour the code no longer has | deleting a comment that is merely terse; wrong is the bar, not brief |
| dead code | nothing reaches it (see above) | a symbol that is only reached in an error path nobody hits in normal use — it is reachable |
| unused declarations | imported, assigned, or declared and never read | a side-effecting import: removing it changes behaviour, and the file rarely says so |
| abandoned config and flags | the branch it selects no longer exists, or has one live value | a flag some environment sets that this repo never mentions |

**Stale is not the same as ugly.** Naming you dislike, a long function, an awkward abstraction —
those are `refactoring`, a different family with a different bar. Say so and leave them.

## What looks dead and isn't

Write `## What must stay` as you go, not at the end. Every candidate you investigate and reject
belongs there with what reaches it.

**It earns its place for THIS sweep, not the next one.** Sweeps deliberately start fresh — inheriting
a judgment means inheriting a stale one, and reachability is exactly the fact most likely to have
changed since. What this section does here and now is three things:

1. **It is the proof the sweep looked.** Twenty removals and nothing rejected is the signature of a
   pass that greped once and stopped; the rejections are where the reachability work is visible.
2. **It stops the removals being trusted too far.** A reader deciding whether to approve a bulk
   deletion needs to know which neighbours were examined and kept, not just what is going.
3. **It names the reaching mechanism**, which is the durable part — "reached by the plugin registry",
   "reached by a naming-rule fixture". A later sweep re-derives the reachability itself, but knowing
   THAT a repo reaches things this way is worth reading.

Write it for a reader deciding today. Anything you would only write to save a future sweep effort
belongs in `## Open threads` instead.

## Suspicious ≠ malicious

Code that surprises you is worth recording plainly: what it does, where it is, why it looked wrong.
Do not soften it into a style note, and do not escalate it into an accusation — most of the time it
is old, or clever, or a workaround whose reason has been lost. **Never remove or disable it as part
of the sweep**: if something genuinely looks unsafe, that is a `security` question and it goes to the
owner as one, at their gate.

## Fan-out

**Split the way the breadth above says, not by habit.** A ONE-AREA sweep splits by kind — one
subagent per kind inside that area. A WHOLE-REPO sweep splits by kind across the tree; give each
reader one kind and the whole tree for it, so the fourth kind gets the same attention as the first.
Splitting a whole-repo sweep by directory is the shape that table warns against, and it is easy to
drift into because directories are the obvious handles — count your readers against the breadth
before you spawn.

Each returns candidates with `file:line` and the searches it ran. You keep the reachability
judgment: a subagent that has read one slice cannot know what the rest of the repo reaches into it,
and that is exactly the mistake that deletes something live.

**In the brief:** the four ways a caller hides from a text search, pasted in full, and the ask —
candidates with the searches that were run, never a deletion list. A subagent given only "find dead
code in `x/`" greps once and returns exactly the list this family exists to not produce.

## The follow-up is the deliverable

Nobody reads a housekeeping report for its findings; they read it to do the removals. So
`## Follow-up work` is ordered for action:

- **Group by safety**, not by directory. Everything provably unreachable in one item; everything
  needing a person's eye in another.
- **Say which are mechanical.** "Delete these 14 unused imports" is one item somebody can approve in
  a minute. Mixing it with a judgment call turns both into a judgment call.
- **Order by landing risk**, so the safe bulk goes first and nothing is blocked behind an argument.

## What housekeeping does NOT do

- **It does not delete.** A research item cannot change code, and that boundary is what makes this
  safe: the sweep proposes, the owner approves, an implementation item removes.
- **It does not tidy.** Renaming, reformatting and restructuring are not this family.
- **It does not guess.** Unprovable reachability is an open thread. Every time.
