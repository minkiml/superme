# Housekeeping — what has gone stale

The item asks what should not be here any more: comments that describe code that changed, dead
functions, unused declarations, abandoned config, anything unaccounted for.

The question is not "is this good code". It is **"does anything still reach this"** — and that has a
provable answer, so this family is graded on proof.

## Contents

- **The bar** — what counts as a finding here
- **Pick your breadth**
- **Start mechanical** — the inventory that produces your shortlist
- **Prove or reject each candidate**
- **What looks dead and isn't**
- **Splitting the work**
- **Shaping the follow-up**
- **What housekeeping does NOT do**

## The bar

A finding is a thing that is stale **plus the proof nothing reaches it**. `grep` returning nothing
is where this work starts, not where it ends.

Four kinds to check in depth are in scope:

| kind | what makes it stale | the recurring miss |
|---|---|---|
| comments and docstrings | it describes behaviour the code no longer has, heavy and overstated comments --- e.g., logs must be be in comments | deleting a comment that is merely terse; wrong is the bar, not brief |
| dead code | nothing reaches it | a symbol reached only in an error path nobody hits — it is reachable |
| unused declarations | imported, assigned or declared and never read | a side-effecting import: removing it changes behaviour, and the file rarely says so |
| abandoned config and flags | the branch it selects no longer exists, or has one live value | a flag some environment sets that this repo never mentions |

**Stale is not the same as ugly.** Naming you dislike, a long function, an awkward abstraction — that
is `refactoring`, a different family with a different bar. Say so and leave them.

## Pick your breadth

The item says whether this is the whole repo or one area. Same bar, different sweep.

| breadth | how you sweep |
|---|---|
| **whole repo** | one KIND at a time, all the way across the tree, then the next |
| **one area** | all four kinds inside it, exhaustively — the breadth where a deletion list can be complete |

State which you got in `## Surface & sample`, in its first line.

## Start mechanical

**Dead code is found by counting, not by reading.** Produce the shortlist mechanically before any
judgment: one pass over the tree, and the only way to cover every declaration rather than the ones
you happened to look at.

1. **List the declarations** — one grep for definition lines across the tree, names only, each
   paired with the file it was declared in.
2. **Count each name's mentions OUTSIDE its own file** — repo-wide, across code AND config, docs,
   templates and scripts, the places a string-reached caller hides.
3. **Zero outside mentions makes it a candidate.** Nothing else does. A name used only within its
   declaring file is as unreachable from outside as one used nowhere at all, so both land on the
   shortlist by the same count and neither needs a judgment call to get there.

Count outside-the-file, never total. A total count sorts names into bands — one mention, two, three
— and the middle bands are too large to check by hand, so they get sampled, and a dead symbol whose
own docstring names it sits in the sample gap. The outside-file count has no middle: it is zero or
it is not.

**Bad and good examples**
```example
✗ per name: grep -rowE "\b<name>\b" . | wc -l    → keep the ones at 1
  (a name mentioned by its own docstring counts 2 and is never looked at again)

✓ grep -rnE "^(def|class) [A-Za-z_]+" --include="*.py" . | sed -E 's/(.*):[0-9]+:(def|class) ([A-Za-z_]+).*/\3 \1/' > syms
  then, per `<name> <file>` pair: grep -rowE "\b<name>\b" . | grep -v "^<file>:" | wc -l
```

Use the equivalent for whatever the tree is written in — exported functions, classes, components,
config keys.

Then run the same pass over FILES, because the name pass cannot see a dead group:

4. **Sweep files, not names.** Walk imports outward from what actually starts the system — the
   entry points, the server's route registrations, the app's root component — and mark every file
   you reach. Any file you never reach is a candidate, and it takes with it everything it imports
   that nothing live imports too.

**A file full of used symbols is dead if its only users are other dead files.** Every name inside a
retired subsystem has callers — its siblings — so every one of them clears the name pass, and the
whole group is invisible to it. This is the largest single deletion a sweep can find and the one it
is most likely to walk past, which is why the file sweep is a step and not a judgment call: a sweep
can return twenty dead symbols and miss the retired subsystem sitting around them.

Where there is no single root to walk from, the pass is the same run backwards: for each file, ask
what imports it, then what imports THAT, until you either reach something the system starts or run
out of files — the second answer is a dead island.

**Both shortlists reach the record — the file pass is step 4, not a replacement for 1–3.** They
find different things and neither is a superset: the name pass finds a dead symbol sitting in a live
file, the file pass finds a live-looking symbol sitting in a dead file. A sweep that walks the graph
and stops has traded every unused assignment, every stale docstring and every orphan helper for one
group. Carry both lists to the end and report from both.

**Write the inventory and the counts into your item folder as you build them**, and read them back
from there afterwards. This pass is the most expensive command in the sweep and its answer does not
change during the run.

**The shortlist is your unit of accounting.** Report how many declarations you inventoried and how
many survived to it, and make sure every survivor appears somewhere in the record: proposed in
`## What can go`, rejected in `## What must stay`, or unresolved in `## Open threads`. A candidate
that appears in none of them reads as one nobody checked.

Judgment starts at the shortlist, not at the tree. Reading files to find candidates and then having
no budget left to prove them is how this sweep goes expensive and incomplete at the same time.

## Prove or reject each candidate

Before anything is called dead, rule out the four ways a caller hides from a text search:

- **Reached by string** — a name in config, a route table, a plugin registry, a task or agent name,
  a template.
- **Reached from outside this repo** — a public API, a CLI entry point, an import by a sibling
  project, a script someone runs by hand.
- **Reached by convention** — a hook, an override, a fixture the test runner collects by naming
  rule, a subclass method the base class calls.
- **Reached indirectly** — reflection, dynamic dispatch, a decorator that registers on import.

Say which you ruled out and how. Anything you cannot prove either way is an OPEN THREAD, never a
deletion — this family's one catastrophic failure is removing something reachable, and leaving it is
always cheaper.

A group off the file sweep gets the same four questions, asked of the GROUP: does anything outside
it name any member — by string, from outside the repo, by convention, indirectly. Its internal
traffic proves nothing, so a member's callers are evidence only when the caller is outside the
group. Expect one or two members to survive: a config module or a helper the live system also
imports. Name them; they are the difference between a clean removal and a broken one.

**Bad and good examples**
```example
✗ "The legacy CLI package is live — 30-odd files reference it, and it has its own entry point."
✓ "Every one of those references is inside the package. Nothing invokes the entry point: no script,
   no CI job, no docs. Dead as a unit — except its config module, which the live server imports."
```

**Where a group dies as a unit, propose it as one item** and name the members that survive it.

## What looks dead and isn't

Write `## What must stay` as you go. Every candidate you investigate and reject belongs there with
**what reaches it** — the reaching mechanism named, not just "it's used".

Twenty removals and nothing rejected is the signature of a sweep that greped once and stopped. The
rejections are where the reachability work is visible, and they are what lets a reader trust the
removals next to them.

Write it for a reader deciding today, not for the next sweep — sweeps start fresh, because
inheriting a judgment means inheriting a stale one, and reachability is the fact most likely to have
moved since. Anything you would only write to save a later sweep effort goes in `## Open threads`.

**Caution**: Code (e.g., function or API) may look dead (unused anywhere within the codebase) when it is actually being used from an external source (e.g., an externally-invoked API like QR code) — check the contents and logic of looking-dead code before calling it dead. Raise it if uncertain with your thought and rationale.

## Splitting the work

Split by KIND, not by directory — one reader per kind, each given the whole tree for it. Directories
are the obvious handles and they are the wrong ones here: split that way and the fourth kind gets
attention in the first two directories and is quietly dropped everywhere after.

Paste the four hiding mechanisms into every brief. A reader given only "find dead code in `x/`"
greps once and returns exactly the list this family exists to not produce.

Readers return candidates with `file:line` and the searches they ran. **You keep the reachability
verdict**: a reader that has seen one slice cannot know what the rest of the repo reaches into it.

## Shaping the follow-up

Nobody reads a housekeeping report for its findings; they read it to do the removals.

- **Group by safety**, not by directory. Everything provably unreachable in one item; everything
  needing a person's eye in another.
- **Say which are mechanical.** "Delete these 14 unused imports" is one item somebody approves in a
  minute. Mixed with a judgment call, both become judgment calls.
- **Order by landing risk**, so the safe bulk goes first and nothing waits behind an argument.

## What housekeeping does NOT do

- **It does not delete.** A research item cannot change code: the sweep proposes, the owner approves,
  an implementation item removes.
- **It does not tidy.** Renaming, reformatting and restructuring are not this family.
- **It does not guess.** Unprovable reachability is an open thread. Every time.
- **It does not disable what looks suspicious.** Record what it does, where it is, and why it looked
  wrong — plainly, without softening it into a style note or escalating it into an accusation. If it
  genuinely looks unsafe, that is a `security` question and goes to the owner as one.
