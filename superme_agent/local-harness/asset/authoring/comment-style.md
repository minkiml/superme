---
name: comment-style
description: The bar for in-code comments and docstrings: when one is earned, the word cap, what must never appear. Pull before a comment pass or a code review.
enabled: true
hub-only: true
---

# Best practice for writing comments

- Comments are for the next engineer reading this file, with no other context.
- **The default is no comment.** The reflex to annotate is the problem, not the length.

## When to write one

- Write one only for a non-obvious **why**: a constraint, a trap, a reason the obvious approach fails.
- One per non-obvious why. Not one per line, not one per field, not one per branch.
- If the code needs a paragraph to justify it, fix the code instead of writing the paragraph.
- If removing the comment loses nothing a reader would have done differently, it was never needed.

## What never earns a comment

- **What the line literally does.** `# increment the counter` above `count += 1`.
- **Anything obvious.** A named constant, a typed field, a one-line getter.
- **A deleted thing.** A note about code that no longer exists is a log entry. Delete it whole.
- **Project history.** Comments describe the code and the system, never how it got here.
- **An excuse for messy code.** Fix it, or file the fix. Do not annotate around it.

**Bad example**
```python
# `author_readiness` and `readiness.md` are RETIRED, replaced by the phase report
def build_report(...):
```

**Good example**
```python
def build_report(...):
```

## What must never appear

- **Internal references.** Ticket codes, slice names, item ids, section numbers, decision ids.
  A fresh session cannot resolve them and the design docs are not published.
- **Dates.** A reader who meets a date starts weighing whether the rule still applies.
- **Names of people.**
- **Doc filenames** that live outside this repo's published surface.

**Bad example**
```python
# Per D-014, the S3 slice moved this out of the runner (see general_docs/loop-redesign.md)
```

**Good example**
```python
# The runner cannot hold this: a retry would replay the write.
```

## Style

- Short and punchy. Plain sentences a guest can read at speed.
- **20 words maximum** for a comment or a docstring.
- A file's top docstring may reach **40 words**, and only when it genuinely needs them.
- Full sentences with a full stop. Two short sentences beat one long one.
- No em dashes, no semicolons, no mid-sentence colons. They read as machine-written.
- No shouting. Do not capitalise a word for emphasis.
- Neutral register. No jokes, no apologies, no hedging.
- One pattern across the whole codebase. A file that reads differently from its neighbours is wrong.

**Bad example**
```python
# NOTE: this is a bit hacky — we replace the body wholesale here; the caller
# then re-reads it, which is unfortunate but necessary given how the writer works.
```

**Good example**
```python
# The caller re-reads the body because the writer replaces it wholesale.
```

## Keeping them true

- An outdated comment is worse than no comment. A reader trusts it and is wrong.
- Change the code, change the comment in the same edit.
- Delete a comment the moment the reason it stated stops being true.
- Renaming a thing means every comment naming it is now a lie. Grep for it.
- A comment or docstring must not omit a critical decision, relation, rule or fact that the section
  applies or raises and that would otherwise be hidden. Where this needs more room, up to another
  20 words is allowed on top of the cap.

**Good example — a critical fact**
```python
# This QR code API is referenced nowhere in this codebase.
# It is a real API, reached externally by the partner integration.
```

## Docstrings

- Same 20-word bar and the same register.
- Say what the function guarantees, not how it works. The body already says how.
- Skip it entirely when the signature already says everything.
- A public API docstring is read by outside consumers. Write it for them, still within the bar.

**Bad example**
```python
def section_body(text, section):
    """This function takes the text of a document and a section name, then it
    searches through the text using a regular expression to find the heading
    that matches, and returns everything underneath it until the next heading."""
```

**Good example**
```python
def section_body(text, section):
    """The body under `## <section>`, or None when the heading is absent."""
```

## The checks

Run before committing:

```bash
PYTHONPATH=scripts/comment_pass python scripts/comment_pass/bar.py --list <files>
```

It reports every comment and docstring over the bar, and flags hard violations. Fix them all.
