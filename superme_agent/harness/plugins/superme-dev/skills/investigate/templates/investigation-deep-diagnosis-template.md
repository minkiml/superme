# Deep diagnosis — {title}

## Questions
<fill:WRITTEN FIRST, before any code. The questions this sweep answers, as questions; then the WALLS, what is in scope and what is explicitly not, naming the area or saying whole-repo; then what DONE means. Update each question's state as you go — answered | partial | open>

## Symptom & reproduction
<fill:THE LOOP FIRST — the ONE command you can name, already run, with its output: what goes red on this bug and green once it is fixed. Then what actually happens, stated so someone else can see it, and how reliably. Say what you cut to minimise it. If you could not build a loop at all, say so plainly, list the rungs you tried, and name the one thing that would unblock it — that is a result. If it does not reproduce, say what varies between the runs that show it and the runs that do not; that variance IS the evidence>

## Hypotheses, ranked
<fill:the 3-5 you wrote BEFORE testing any of them, in the order you ranked them, each with the prediction that would falsify it ("if X, then changing Y makes it disappear"). Mark which you put to the owner and what they said, or that you proceeded on your own ranking>

## What was ruled out
<fill:each hypothesis you eliminated and what eliminated it. Separate the confident eliminations from the ones you would revisit. Worth writing even when the mechanism is never found>

## The mechanism
<fill:the narrowest cause you located, with the path from trigger to symptom and file:line at each step. Then the strength of the claim, in these words: "this is where it diverges" (observed) · "this is why it diverges" (explained) · "this would explain it" (hypothesis, unconfirmed)>

## Follow-up work
<fill:the fix this implies, and whether it is obvious enough to file directly or needs its own plan. Also what would stop this class of bug rather than this instance — that is usually the more valuable item>

## Open threads
<fill:what you could not determine and what would settle it (a log you do not have, a run you cannot reproduce), plus anything outside the Boundaries — or "none">
