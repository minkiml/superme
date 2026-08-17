# Housekeeping — what has gone stale

The item asks what should not be here any more: comments that describe code that changed, dead
functions, unused declarations, abandoned config, anything unaccounted for.

The question is not "is this good code". It is **"does anything still reach this"** — and that has a
provable answer, so this family is graded on proof.

## The bar

A finding is a thing that is stale **plus the proof nothing reaches it**. `grep` returning nothing
is where this work starts, not where it ends.

Four kinds to check in depth are in scope:

| kind | what makes it stale | the recurring miss |
|---|---|---|
| comments and docstrings | it describes behaviour the code no longer has, or carries weight a comment should not: history, narration, an excuse for the code beneath it | deleting a comment that is merely terse; wrong or heavy is the bar, not brief |
| dead code | nothing reaches it | a symbol reached only in an error path nobody hits — it is reachable |
| unused declarations | imported, assigned or declared and never read | a side-effecting import: removing it changes behaviour, and the file rarely says so |
| abandoned config and flags | the branch it selects no longer exists, or has one live value | a flag some environment sets that this repo never mentions |

**Stale is not the same as ugly.** Naming you dislike, a long function, an awkward abstraction — that
is `refactoring`, a different family with a different bar. Say so and leave them.

## What stale actually looks like

The four kinds say what the bar is. These are the shapes that keep turning up inside them — the
list to hold in your head while you sweep, so you recognise one rather than derive it. Each reads
*what it is* → *what to do*. Not exhaustive, and a repo's own history will add to it.

- **Retired subsystem** — a group of files that only reference each other, superseded by something
  newer, often with a note somewhere already saying so. → propose as ONE removal, naming the
  members the live system still imports.
- **Superseded generation** — a second implementation of the same surface, left beside the one that
  replaced it (an old page tier, a v1 client, a pre-migration module). → confirm which is wired,
  remove the other whole.
- **Compatibility shim** — a wrapper, alias or re-export kept "so old callers keep working", with no
  old callers left. → remove it and the indirection it was hiding.
- **Landed flag** — a feature flag, env switch or config branch whose other branch no longer exists,
  or that every environment now sets the same way. → delete the branch and the flag together.
- **Renamed-in-code-only** — a variable, key or path the code reads under a new name while the docs,
  example files or deploy config still name the old one. → fix the document; the code is the truth.
- **Vestigial parameter** — an argument, field or option every caller passes identically, or that
  nothing downstream reads. → drop it from the signature and the call sites.
- **Commented-out code** — a block preserved in comments "in case". → delete it; version control is
  the case.
- **Orphaned test material** — a fixture, mock, snapshot or test file for code that no longer
  exists. → remove with whatever it tested.
- **Unpulled dependency** — a package pinned in the manifest that nothing imports. → drop the pin,
  after checking it is not a transitive or tooling-only requirement.
- **Parked artefact** — files named as drafts or backups (e.g., `*.old`, `*.bak`, `_v2`, `_new`,
  `copy of`) sitting in the source tree. → remove, or explain why one is load-bearing.
- **Aspirational comment** — a docstring or note describing behaviour that was planned, narrowed, or
  never built, including a TODO pointing at work that shipped or was abandoned. → rewrite it to what
  the code does, or delete the note.
- **Outgrown docstring** — accurate when written, now describing a fraction of what the thing does.
  → the recurring one, and the easiest to read past: check the docstring against the whole surface,
  not against the first method under it.
- **Log in a comment** — a comment carrying history: what changed, when, who asked, which incident.
  → version control holds that. Delete the narration; keep only the rule it happened to state, in
  the present tense.
- **Restating & Obvious comment** — prose saying in words what the line beneath it says in code. → delete it.
  If the WHY is missing, that is what should be there instead.
- **Excusing comment** — a paragraph explaining why the code below is confusing, instead of the code
  being less confusing. → the comment is evidence, not the finding: name the code as a refactoring
  candidate and say so.

**What a comment owes.** Use this to judge one, and to say what should replace it:

- the WHY — never a restatement of the line below it
- short enough to take in at a glance
- true today, or gone; an outdated comment is worse than none
- no history — what changed, when, and who asked lives in version control
- no excuses — if code needs a paragraph to be understandable, the finding is the code
- no overstatement - never comment for ovbious and clear things.

## What is worth reporting

A sweep that reports everything it proves has still not done the ranking a reader needs, and
housekeeping findings differ enormously in worth. Rank by how much the finding costs the people
working in this repo:

| worth | what it looks like |
|---|---|
| **high** | a whole dead group; anything that misleads a reader into believing something untrue about live code — a comment, a doc, a flag |
| **medium** | dead symbols with real weight behind them: a helper, a page, a route, a dependency |
| **low** | single unused imports, dead loggers, one-line drift |

Low-worth findings are still worth reporting — they are free to remove and they batch — but they go
in one grouped item, never interleaved with the ones a person must think about. Twenty imports
listed ahead of a retired subsystem buries the only finding that mattered.

## Pick your breadth

The item says whether this is the whole repo or one area. Same bar, different sweep.

| breadth | how you sweep |
|---|---|
| **whole repo** | one KIND at a time, all the way across the tree, then the next |
| **one area** | all four kinds inside it, exhaustively — the breadth where a deletion list can be complete |

State which you got in `## Surface & sample`, in its first line.

## Start mechanical

Build the shortlist by counting, before reading anything. Two passes, both required: the name pass
finds a dead symbol in a live file, the file pass finds a live-looking symbol in a dead file.
Neither is a superset of the other.

**Names**

0. **Exclude what nobody hand-wrote** before you count anything — generated clients and schema
   types, vendored dependencies, build output, lockfiles. A dead export in a generated file is not
   a finding; it is regenerated next build. State the exclusion and its size, so the inventory's
   denominator is the code somebody actually maintains.
1. List every declaration, each paired with the file it was declared in.
2. Count each name's mentions **outside its own file** — across code, config, docs, templates and
   scripts, where a string-reached caller hides.
3. Zero outside mentions makes it a candidate. Nothing else does.

Never count total mentions: a total sorts names into bands, the middle bands are too big to check by
hand and get sampled, and a dead symbol whose own docstring names it sits in the sample gap.

One walk, not one per name — collect every identifier occurrence into `name → files`, then read the
candidates off it.

**Files**

4. Walk imports outward from what starts the system: entry points, route registrations, the root
   component. Any file never reached is a candidate, and it takes with it whatever only it imports.

A file full of used symbols is dead when its only users are other dead files — every name in a
retired subsystem has callers, its siblings, so the whole group clears the name pass invisibly.
Where there is no single root, run the walk backwards: what imports this, then what imports that,
until you reach something that starts or run out of files.

**Build both inventories ONCE, into your scratch directory, before any reader exists**, and hand
every reader the path and the file names. These are the sweep's most expensive commands and their
answers do not change during the run; a reader that enumerates the tree itself is redoing your work
on a fraction of your budget.

**Every name that reached a shortlist appears in the record** — proposed in `## What can go`,
rejected in `## What must stay`, or unresolved in `## Open threads`. One that appears in none reads
as one nobody checked.

## Prove or reject each candidate

Rule out the four ways a caller hides from a text search, and say which you ruled out and how:

- **By string** — config, a route table, a plugin registry, a task or agent name, a template.
- **From outside this repo** — a public API, a CLI entry point, a sibling project, a script somebody
  runs by hand.
- **By convention** — a hook, an override, a fixture the runner collects by naming rule, a subclass
  method the base class calls.
- **Indirectly** — reflection, dynamic dispatch, a decorator that registers on import.

Anything you cannot prove either way is an open thread, never a deletion: removing something
reachable is this family's one catastrophic failure, and leaving it is always cheaper. The second
mechanism is the trap — an endpoint invoked only from outside the repo looks dead from every angle a
search can see, so read what a candidate DOES before calling it dead.

Ask the same four of a GROUP. Its internal traffic is not evidence; a member's caller counts only
from outside the group. Expect one or two members to survive — a config module, a helper the live
system also imports — and name them.

**Bad and good examples**
```example
✗ "The legacy CLI package is live — 30-odd files reference it, and it has its own entry point."
✓ "Every one of those references is inside the package. Nothing invokes the entry point: no script,
   no CI job, no docs. Dead as a unit — except its config module, which the live server imports."
```

## What looks dead and isn't

Write `## What must stay` as you go: every candidate you rejected, with the reaching mechanism named
— not "it's used". Twenty removals and nothing rejected is a sweep that greped once and stopped.

Write it for a reader deciding today, not for the next sweep: reachability is the fact most likely
to have moved by then. Anything you would only write to save a later sweep goes in `## Open threads`.

## Splitting the work

**Split by KIND first — never by directory**, or the fourth kind gets attention in two directories
and is dropped everywhere after.

**Then rebalance by SIZE from the census.** The kinds differ in cost by an order of magnitude: dead
code and unused declarations each walk the whole declaration inventory, while abandoned config is a
bounded list of env vars, pins and config files. Pair the small kinds under one reader and cut the
large kind by area — the kind stays whole across its readers, so nothing is dropped. Keep slices
within roughly 2× of each other; an overloaded reader returns less rather than failing.

Every brief carries the four hiding mechanisms, the scratch path and the census file names. A reader
given only "find dead code in `x/`" greps once and returns the list this family exists to not
produce.

Readers return candidates with `file:line` and the searches they ran. **You keep the reachability
verdict** — a reader who has seen one slice cannot know what the rest of the repo reaches into it.

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
