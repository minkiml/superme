# A standard prinple in writing skill artifacts (skill and all its packaged files)

- A SKILL IS A RECIPE. A specialist's procedure card for one repeatable
  job: do this, then this, check that. Not a description of a role, not a
  briefing on the system, not a policy document.

- INSTRUCT; DO NOT EXPLAIN THE SYSTEM. How the harness fires the run, what
  a gate counts, what the SDK does on spawn — none of it changes what the
  agent does next. Test: if the sentence would still be true with the
  agent removed from the picture, it is not instruction.

- EVERY LINE IS AN ACTION OR A CHECK ON AN ACTION. Anything that is
  neither is commentary. Cut it.

- ONE RULE, ONE HOME. A skill folder holds several kinds of file, and each
  owns exactly one thing:

```
    SKILL.md   — Main skill file the sequence: what to do, in what order

    /reference/  — the method and the bar for one specialised case

    /template/   — organization, strucure, and what the produced artifact should contain and be like.

    /agent/*.md      — how a delegated worker of the skill works and what it hands back (agent declaration with instruction, system prompt, persona, and etc.)

    /scripts/    - Executable code: Deterministic scripts to execute locally using terminal tools to run checks or modify environments during the skill.

    /assets/     - acts as a lazy-loaded storage unit for static files and supporting resources 

  A rule stated in two of them will drift in one, and the reader cannot
  tell which copy is current.
```

- NUMBER THE STEPS IN EXECUTION ORDER, and if the skill ships a
  copy-this checklist, make it match the steps 1:1. Ordering mistakes
  become visible instead of arguable — a step that writes to a file the
  next step creates is obvious in a numbered list and invisible in prose.

- GIVE A REASON ONLY WHERE IT CHANGES THE ACTION, as a clause, never a
  paragraph. Justifying every rule doubles the length and trains the
  reader to skim.

- LABEL EVERY EXAMPLE, AND KEEP IT GENERIC.
  **Good example** / **Bad example** / **Bad and good examples** on the
  line above the fence. Invented names — never this repo's real files, or
  the agent copies the illustration instead of the shape. An unlabelled
  illustration reads as instruction.

- A CHECKABLE TEST BEATS AN EXHORTATION. Give a question with a yes/no
  answer, or a criterion a reader could verify — not "be thorough", "be
  careful", "use good judgment". Those produce no change in behaviour.

- NAME THE EXACT STRING. File paths, tool names, commands, identifiers,
  section headings — verbatim, and only if they actually exist. Verify
  each one before shipping. A paraphrase that silently resolves to
  something else is worse than an error.

- STATE PROHIBITIONS ONLY WHERE THE TEMPTATION IS REAL. Three or four at
  the points the job actually goes wrong. A list of everything not to do
  is a list nobody reads.

- NO DATES, NO INCIDENT LOGS, NO COMMENTS. A skill instructs; it never
  records its own history. A reader who meets a date starts weighing
  whether the rule still applies.

- EACH FILE STANDS ALONE. Write for someone who has read nothing else,
  because in a fresh context that is exactly who arrives.

- DENSE, NOT TERSE. Cut words, keep rules. If removing a sentence loses
  nothing an agent would have done differently, it was never load-bearing.