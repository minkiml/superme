<!-- Close report: reports/report-close.md — the last thing the owner reads about this item.
     Keep it ≤ 1 screen; tables over paragraphs. Every fact is checked against the repo and the
     item record — a claim that doesn't hold bounces with an itemized rejection. -->
# Close — <fill:item title>

**What landed:** <fill:the merged change in 1–3 lines, for someone reading the trail months later — what is now true of main that wasn't before>

**Facts:**
| | |
|---|---|
| merge commit | <fill:the recorded merge commit, or `none — never merged`> |
| files changed | <fill:count + the real list from `git diff --name-only <base>..<branch>`, or `none`> |
| tests run | <fill:what actually ran and its result, or `none`> |

**Knowledge:** <fill:which anchor docs this item updated and what they now say — or `none needed` with the reason>

**Skipped, and why:** <fill:denied authorizations, dropped tasks, deliberate gaps — each with the reason it was left. Write `nothing skipped` only if that is true.>

**Loose ends:** <fill:spawned items, follow-ups filed to the inbox, open children — with ids. `none` if clean.>
