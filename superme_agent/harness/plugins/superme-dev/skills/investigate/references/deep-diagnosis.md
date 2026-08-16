# Deep diagnosis — what is the mechanism

The item names a behaviour nobody can explain and asks why it happens.

## Contents

- **Check this is the right family**
- **Build a tight loop that goes red** — everything waits behind this
- **Ways to build one**, in order
- **When you genuinely cannot build one**
- **Rank the hypotheses before you test one**
- **Narrow, don't browse**
- **Ruling out is evidence**
- **State the mechanism, and how far the evidence goes**
- **Splitting the work**
- **Shaping the follow-up** — the fix, and the class
- **What a deep diagnosis does NOT do**

## Check this is the right family

A deep diagnosis is a work-item with a gate. It earns that only when at least one of these holds:

- **The symptom spans runs, sessions or components** — no single trace contains the answer.
- **It is intermittent** — the work is in finding what varies, which takes more than one look.
- **The obvious explanation has already failed.** Someone looked, was confident, and was wrong.
- **Answering it means instrumenting or measuring**, not just reading.

If none hold, say so in your report. A single run's trace, read directly, may be the whole answer,
and a cheap answer beats an expensive one.

## Build a tight loop that goes red

**This is the family.** Once you have one command that goes red on this bug and green when it is
fixed, bisection, elimination and instrumentation all just consume it. Without one, reading code
produces a plausible cause — and plausible-and-unverified looks exactly like solved.

Spend the run here disproportionately. A tight loop is four things:

- **Red-capable** — it drives the real code path and asserts the OWNER'S EXACT SYMPTOM, not "it ran".
- **Deterministic** — same verdict every time. For an intermittent bug, a pinned high rate.
- **Fast** — seconds. A 2-second certain loop is a different tool from a 30-second flaky one.
- **Yours to run** — unattended, from your own item folder, as many times as you like.

**The gate: no red command, no hypothesis.** Before you write a cause anywhere, you can name one
command, show it run at least once, and show its output. If you catch yourself reading code to build
a theory before that command exists, go back and build the loop.

### Ways to build one — try them in roughly this order

1. **A script in your item folder that calls the code path directly.** Import it, feed it the input,
   assert the symptom: `cd <item-dir> && python3 repro.py`.
2. **Replay a captured run** — the trace, the payload, the event log, the stored row. Real input,
   replayed in isolation, with none of the rest of the system moving.
3. **A differential.** The run that fails beside the run that does not, same shape, diffed. Often
   faster than understanding either one.
4. **A CLI or HTTP invocation** against a running instance, diffed against a known-good output.
5. **Bisect something** — commits, input size, config values, the sequence of steps. Each halving is
   worth more than an hour of reading.
6. **A property or fuzz loop** when the symptom is "sometimes wrong": a thousand inputs, looking for
   the failure shape.
7. **Raise the rate** when it is timing-dependent: repeat, parallelize, inject sleeps, add load. The
   goal is not a clean repro but a HIGHER REPRODUCTION RATE — 50% is debuggable, 1% is not.

You cannot write a test into the repo — this item is read-only on real code, which is why rung 1 is a
script in your own folder. If the honest fix is a permanent regression test, that is
`## Follow-up work`.

### When you genuinely cannot build one

Say so plainly and stop. Do not hypothesise anyway. List what you tried, rung by rung, and what each
one hit. Then name the ONE thing that would unblock it: the log that does not exist, the
instrumentation someone would have to add, the environment you cannot reach.

**If it does not reproduce at all, that IS the investigation for now.** Collect the runs that show it
and the runs that do not, and diff their conditions — inputs, timing, ordering, concurrency,
environment, data shape, code version. The variance is the evidence.

## Rank the hypotheses before you test one

Once the loop is red, write **3–5 hypotheses, ranked, before testing any of them.** Generating one at
a time anchors you on whichever plausible idea arrived first.

Each states its **prediction**, so it can be wrong:

> If X is the cause, then changing Y makes the symptom disappear / changing Z makes it worse.

A hypothesis you cannot write a prediction for is a vibe — sharpen it or drop it.

**Then put the ranked list to the owner before you spend the run on #1**, through the ask surface,
and keep working while you wait. They often re-rank it in one line, and that line is worth more than
an hour of elimination. If no answer comes, proceed on your own ranking and say in the record that
you did.

## Narrow, don't browse

The loop is red; now spend the run halving. Every move here is one the loop pays for — you make a
change, you run it, it tells you.

- **Tighten the loop before you use it hard.** Cache the setup, cut unrelated init, assert the exact
  symptom instead of "it failed", pin the clock and the seed. Every later step runs this loop dozens
  of times.
- **Shrink the repro to what is load-bearing.** Cut inputs, callers, config and steps ONE at a time,
  re-running after each cut. Done when removing any remaining piece turns it green.
- **Follow the data, not the control flow.** Where does the wrong value first exist? Everything
  upstream of that is out of scope, and saying so is how the surface shrinks.
- **Instrument one variable at a time.** Each probe answers one prediction from your ranked list;
  changing two things at once buys an ambiguous result at the same price as a clean one. Probes and
  scripts stay in your own item folder: `cd <item-dir> && python3 probe.py`.
- **Trust the trace over the story** — including your own reading of the code.

## Ruling out is evidence

Every hypothesis you eliminate goes in `## What was ruled out` with what eliminated it, as you go.

A diagnosis that finds the mechanism and lists nothing ruled out is unverifiable — the reader cannot
tell whether you converged or guessed first and got lucky. When the mechanism is NOT found, the
eliminations are the entire deliverable.

Record the confident eliminations *and* the ones you would revisit. They are different.

## State the mechanism, and how far the evidence goes

Give the narrowest cause you located, with the path from trigger to symptom and `file:line` at each
step. Then be exact about the strength of the claim:

- **"This is where it diverges"** — observed. You saw the wrong value appear here.
- **"This is why it diverges"** — explained. You can say which code produces it and under what
  condition.
- **"This would explain it"** — a hypothesis consistent with the evidence, not yet confirmed.

Use those words. Promoting the third to the second is the failure mode of this family.

## Splitting the work

Diagnosis parallelizes badly — it is sequential narrowing, and each step depends on the last. Split
only for genuinely independent legs: several candidate subsystems to eliminate at once, or several
historical runs to characterize.

Paste the three strength-of-claim words into every brief — observed · explained · would-explain — and
ask for observations verbatim: the trace, the values, the conditions. A leg that saw one subsystem
and returns a mechanism is handing you a guess with a citation attached.

**Every elimination decision stays yours.**

## Shaping the follow-up

`## Follow-up work` carries two things:

- **The fix this implies**, and whether it is obvious enough to file directly or needs its own plan.
  A one-line fix at a mechanism you have proven is filable; a fix you are guessing at is a plan.
- **What would stop this CLASS of bug** — a check, a test, a type, an invariant, a piece of
  instrumentation that would have made this visible in an hour. Usually the more valuable item, and
  the one nobody files unless the diagnosis names it.

If the mechanism was not found, the follow-up is what would settle it.

## What a deep diagnosis does NOT do

- **It does not fix.** Even when the fix is one line and you are certain.
- **It does not stop at the first plausible cause.** Plausible is where the work starts.
- **It does not report a hypothesis as a mechanism.** The words above exist to keep those apart.
