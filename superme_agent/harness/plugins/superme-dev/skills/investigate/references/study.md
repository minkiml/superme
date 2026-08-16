# Study — what should we take from something built elsewhere

The item names something outside this repo — another codebase, a library we might depend on, a
product whose design we want, a published method or page — and asks what we should take from it.

## The bar

Two claims, and they never share a sentence:

- **What they do** is evidence — anyone reading the same snapshot can check it.
- **What we should take** is judgment — the owner decides on it.

**Bad and good examples**
```example
✗ "They cache the parsed tree, so we should too."
✓ "`parser.py:88` caches the parsed tree keyed on file mtime (v4.2.0). — Taking it here would suit
   our own `load_document`, which re-parses on every call; it survives the scale test at our sizes."
```

## Pin the source

The first thing in `## Source` is which snapshot you read: a repo's commit, a library's exact version
**and** the version we would resolve to, a page's URL and the date.

Keep two kinds of source apart. What they SAY they do — README, design doc, talk — is evidence about
intent. The code and its behaviour are evidence about the thing. When the two disagree the code wins,
and the disagreement is usually the most interesting line in the study.

## Apply the transfer test

An observation becomes a proposal once you have said what could void it:

- **Scale** — at their input sizes, not ours. What pays for itself at their volume can be pure
  overhead here, and the reverse.
- **Constraints** — what they must satisfy that we don't (a public API they can't break,
  multi-tenancy, a platform target), and what we must satisfy that they don't.
- **Cost to run it** — a pattern a large team maintains is not free here because its code is small.
- **What it lands on HERE** — our file, our subsystem, named. Unnamed, the proposal is untestable.

Survives none of them? It stays in `## What they do`, and saying so is a result.

## Copying is a licence question

Reading is free. A block of their source in our tree is a licence question and not yours to settle:
it goes in the report as a proposal with the licence named, never as a quiet paste. Describe the
mechanism in your own words and there is nothing to settle.

## Where to start

| studying | start at | the recurring miss |
|---|---|---|
| a codebase | its shape — entry points and module boundaries — before any single file | mistaking the newest layer for the design; check whether you are reading the architecture or the third rewrite of one corner |
| a library we might depend on | its tests and its issue tracker | judging it by the README's happy path — the failure modes we would inherit live in the issues |
| a product's design | the path a user actually walks, end to end | copying the surface without the constraint that produced it |
| a paper, spec or dataset | the claim, and the conditions it holds under | quoting a result whose conditions we cannot reproduce as if it were ours |

## Splitting the work

One reader per source, or per subsystem inside a large one. Each returns what that source does, with
a pointer.

Paste into every brief what pins a source — a commit, an exact version, a URL and the date read —
and the split between what they SAY they do and what their code does. Ask for that split by name; a
reader working from a README will otherwise hand you the intent and call it the thing.

**The comparison stays yours.** A reader that has only read them cannot know what transfers, because
it has not read us.

## Shaping the follow-up

`## Follow-up work` says what taking it would actually cost here: items in landing order, each naming
what it touches in OUR code.

**Bad and good examples**
```example
✗ "Adopt their caching approach."
✓ "Add a parse cache keyed on file mtime in `documents.load_document` — ~1 file."
```

Size it honestly. If the honest answer is that adopting it is a month, say a month.

## What a study does NOT do

- **It does not adopt.** Nothing changes in our code because a study liked it — that is the owner's
  call at the review gate.
- **It does not file work.** An idea is an open thread, then a proposal in the report.
- **It does not audit them.** Their bugs are theirs, unless we would inherit one — then name it and
  say what we would inherit.
- **It does not bring the material home.** A clone or a bulk dataset has no place in the item folder;
  raise it as a wall.
