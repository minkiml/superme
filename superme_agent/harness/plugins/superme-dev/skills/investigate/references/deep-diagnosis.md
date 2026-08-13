# Deep diagnosis — what is the mechanism

Read before a deep diagnosis: the plan names a behaviour nobody can explain and asks why it happens.

## Contents

- **What makes this DEEP** — and when to use the quick diagnosis instead
- **Reproduction is the first result**
- **Narrowing** — how to spend the run
- **Ruling out is evidence**
- **The mechanism, and how far the evidence goes**
- **Fan-out**
- **The follow-up: the fix, and the class**
- **What a deep diagnosis does NOT do**

## What makes this DEEP

SuperMe already has a quick diagnosis: a session opened on ONE run, with that run's trace injected,
launched from its Activity row. It answers "what happened in this run", it is cheap, and it is the
right tool most of the time.

A deep diagnosis is a planned work-item with a gate, and it earns that only when at least one of
these is true:

- **The symptom spans runs, sessions or components** — no single trace contains the answer.
- **It is intermittent** — the work is in finding what varies, which takes more than one look.
- **The obvious explanation has already failed.** Someone looked, was confident, and was wrong.
- **Answering it means instrumenting or measuring**, not just reading.

If none of those hold, say so in your report: the honest outcome may be that this needed a quick
diagnosis, and a cheap answer beats an expensive one.

## Reproduction is the first result

Before any hypothesis, establish what actually happens, stated so someone else sees the same thing:
the exact trigger, what appears, and how reliably.

**If it does not reproduce, that IS the investigation for now.** Collect the runs that show it and
the runs that do not, and diff their conditions — inputs, timing, ordering, concurrency, environment,
data shape, which code version. The variance is the evidence, and narrowing it is progress even when
the mechanism is still hidden.

A diagnosis that skips this and goes straight to reading code usually finds a plausible cause. That
is the danger: plausible and unverified looks exactly like solved.

## Narrowing

Spend the run halving, not browsing:

- **Bisect what you can** — commits, inputs, config, the sequence of steps. Each bisection is worth
  more than an hour of reading.
- **Follow the data, not the control flow.** Where does the wrong value first exist? Everything
  upstream of that is out of scope, and saying so is how the surface shrinks.
- **Instrument when reading stalls.** Throwaway scripts and probes are fine, scoped into your own
  item folder (`cd <item-dir> && python3 probe.py`) — a research item is read-only on real code, and
  that holds for the shell too.
- **Trust the trace over the story.** A logged sequence beats anyone's account of what the code does,
  including your own reading of it.

## Ruling out is evidence

Every hypothesis you eliminate goes in `## What was ruled out` with what eliminated it, as you go.

This is the section that earns this family its own shape. A diagnosis that finds the mechanism and
lists nothing ruled out is unverifiable — the reader cannot tell whether you converged or guessed
first and got lucky. And when the mechanism is NOT found, the eliminations are the entire deliverable:
they are what stops the next run re-walking the same dead ends.

Record the confident eliminations *and* the ones you would revisit. They are different.

## The mechanism, and how far the evidence goes

State the narrowest cause you located, with the path from trigger to symptom and `file:line` at each
step. Then be precise about the strength of the claim:

- **"This is where it diverges"** — observed. You saw the wrong value appear here.
- **"This is why it diverges"** — explained. You can say which code produces it and under what
  condition.
- **"This would explain it"** — a hypothesis consistent with the evidence, not yet confirmed. Say so
  in those words; the temptation to promote this sentence to the one above is the whole failure mode
  of the family.

## Fan-out

Diagnosis parallelizes badly — it is sequential narrowing, and each step depends on the last. Fan out
only for genuinely independent legs: several candidate subsystems to eliminate at once, or several
historical runs to characterize. Each subagent returns what it OBSERVED, never a verdict, and you
keep every elimination decision.

## The follow-up: the fix, and the class

`## Follow-up work` carries two things:

- **The fix this implies**, and whether it is obvious enough to file directly or needs its own plan.
  A one-line fix at a mechanism you have proven is a filable item; a fix you are guessing at is a
  plan.
- **What would stop this CLASS of bug**, not this instance — a check, a test, a type, an invariant,
  a piece of instrumentation that would have made this visible in an hour. This is usually the more
  valuable item, and it is the one nobody files unless the diagnosis names it.

If the mechanism was not found, the follow-up is what would settle it: the log that does not exist,
the instrumentation to add, the reproduction to build.

## What a deep diagnosis does NOT do

- **It does not fix.** Even when the fix is one line and you are certain.
- **It does not stop at the first plausible cause.** Plausible is where the work starts.
- **It does not report a hypothesis as a mechanism.** The words above exist to keep those apart.
