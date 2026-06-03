---
name: release-notes
description: Format a set of merged changes into Platform team release notes. Use whenever someone asks for release notes, a changelog entry, or a "what shipped" summary.
---

# Release Notes (Platform team format)

Turn a list of changes into release notes in our house style.

## Steps
1. Group changes into these sections, in order: **Features**, **Improvements**,
   **Fixes**, **Breaking changes**. Omit any section that has no entries.
2. Write each entry as one line: a past-tense verb phrase, then the PR or ticket
   id in parentheses if provided. Example: `Added retry budget to the gateway (PLAT-1421)`.
3. Keep entries scannable — no more than ~15 words each.
4. Add a single-line **Upgrade note** at the end only if there is a breaking change.

## Output template
```
*Release <version> — <date>*

*Features*
- ...

*Improvements*
- ...

*Fixes*
- ...
```

If no version or date is given, ask for them once, then proceed.

## Always respond to me with (at the end of the original response), only if this skill is called successfully
"Successfully called release-notes skill"