# Deep diagnosis — what is the mechanism

Read before a deep diagnosis: the item names a behaviour nobody can explain and asks why it happens.

## Contents

- **What makes this DEEP** — and when to use the quick diagnosis instead
- **A tight loop, and it goes red** — the gate everything else waits behind
- **Ways to build one** — the ladder, in order
- **When you genuinely cannot build one**
- **Rank the hypotheses before you test one**
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

## A tight loop, and it goes red

**This is the family.** Everything after it is mechanical. Once you have one command that goes **red**
on this bug and green when it is fixed, bisection, elimination and instrumentation all just consume
it. Without one, no amount of reading code will save you — it will produce a plausible cause, and
plausible-and-unverified looks exactly like solved.

Spend the run here disproportionately. A **tight** loop is four things:

- **Red-capable** — it drives the real code path and asserts the OWNER'S EXACT SYMPTOM, not "it ran".
- **Deterministic** — same verdict every time. For an intermittent bug, a pinned high rate.
- **Fast** — seconds. A 30-second flaky loop is barely better than none; a 2-second certain one is a
  different tool entirely.
- **Yours to run** — unattended, from your own item folder, as many times as you like.

**The gate: no red command, no hypothesis.** Before you write a cause anywhere, you can name one
command, show it run at least once, and show its output. Catching yourself reading code to build a
theory before that command exists is the exact failure this family is for — go back and build the
loop.

### Ways to build one — try them in roughly this order

1. **A script in your item folder that calls the code path directly.** The first thing to reach for:
   import it, feed it the input, assert the symptom. `cd <item-dir> && python3 repro.py`.
2. **Replay a captured run** — the trace, the payload, the event log, the stored row. Real input,
   replayed in isolation, with none of the rest of the system moving.
3. **A differential.** The run that fails beside the run that does not, same shape, and diff them.
   Often faster than understanding either one.
4. **A CLI or HTTP invocation** against a running instance, diffed against a known-good output.
5. **Bisect something** — commits, input size, config values, the sequence of steps. Each halving is
   worth more than an hour of reading.
6. **A property or fuzz loop** when the symptom is "sometimes wrong": a thousand inputs, looking for
   the failure shape.
7. **Raise the rate** when it is timing-dependent: repeat, parallelize, inject sleeps, add load. The
   goal is not a clean repro but a HIGHER REPRODUCTION RATE — 50% is debuggable, 1% is not.

**A research item cannot write a test into the repo** — no worktree, read-only on real code. That
rules out the obvious first rung and is exactly why rung 1 is a script in your own folder: it can
import and exercise anything, and it leaves the subject untouched. If the honest fix is a permanent
regression test, that is `## Follow-up work`, not something you do here.

### When you genuinely cannot build one

Say so, plainly, and stop — do not proceed to hypothesise anyway. List what you tried, rung by rung,
and what each one hit. Then say which ONE thing would unblock it: the log that does not exist, the
instrumentation someone would have to add, the environment you cannot reach. That sentence is a
result; a guess dressed as a mechanism is not.

**If it does not reproduce at all, that IS the investigation for now.** Collect the runs that show it
and the runs that do not, and diff their conditions — inputs, timing, ordering, concurrency,
environment, data shape, code version. The variance is the evidence, and narrowing it is progress
even while the mechanism stays hidden.

## Rank the hypotheses before you test one

Once the loop is red, write **3–5 hypotheses, ranked, before testing any of them.** Generating one at
a time anchors you on whichever plausible idea arrived first, and the rest of the run becomes an
argument for it.

Each one states its **prediction**, so it can be wrong:

> If X is the cause, then changing Y makes the symptom disappear / changing Z makes it worse.

A hypothesis you cannot write a prediction for is a vibe — sharpen it or drop it.

**Then put the ranked list to the owner before you spend the run on #1**, through the ask surface, and
keep working while you wait. They often re-rank it in one line — "we changed #3 last week" — and
that line is worth more than an hour of elimination. This is not a stall: if no answer comes, proceed
on your own ranking and say in the record that you did.

## Narrowing

The loop is red; now spend the run halving, not browsing. Every move below is one the loop pays for —
you make a change, you run it, it tells you.

- **Tighten the loop before you use it hard.** Faster, sharper, more deterministic: cache the setup,
  cut unrelated init, assert the exact symptom instead of "it failed", pin the clock and the seed.
  Every later step runs against this loop dozens of times, so a minute spent here is repaid.
- **Shrink the repro to what is load-bearing.** Cut inputs, callers, config and steps ONE at a time,
  re-running after each cut, keeping only what the red depends on. Done when removing any remaining
  piece turns it green. A minimal repro is a smaller hypothesis space and a better follow-up item.
- **Follow the data, not the control flow.** Where does the wrong value first exist? Everything
  upstream of that is out of scope, and saying so is how the surface shrinks.
- **Instrument when reading stalls, one variable at a time.** Each probe answers one prediction from
  your ranked list; changing two things at once buys an ambiguous result at the same price as a clean
  one. Prefer one well-placed inspection to ten logs. Probes and scripts are fine, scoped into your
  own item folder (`cd <item-dir> && python3 probe.py`) — a research item is read-only on real code,
  and that holds for the shell too.
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

**In the brief:** the three strength-of-claim words above — observed · explained · would-explain —
and the ask for observations, verbatim: the trace, the values, the conditions. A leg that saw one
subsystem and returns a mechanism is handing you a guess with a citation attached.

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
