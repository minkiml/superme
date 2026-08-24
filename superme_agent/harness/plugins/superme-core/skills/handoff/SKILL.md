---
name: handoff
description: Compact the current conversation into a handoff document for another agent to pick up.
argument-hint: "What will the next session be used for?"
category: general 
---

# Handoff

Compact the current conversation into a handoff document a fresh agent can pick up from.

## Steps
1. **Scope it.** If the user passed an argument, treat it as what the next session will focus on and
   tailor the doc to that; otherwise cover the whole conversation.
2. **Write the doc** with these sections: **Goal** (what the next session is for) · **State** (what's
   done, what's in progress, what's blocked) · **Key decisions** (with their why) · **Next steps**
   (concrete, ordered) · **Suggested skills** (the skills the next agent should invoke).
3. **Reference, don't duplicate.** Point at existing artifacts (PRDs, plans, ADRs, issues, commits,
   diffs) by path or URL instead of restating them.
4. **Redact secrets** — no API keys, passwords, or PII.
5. **Save** to the OS temp directory (not the current workspace), and report the path.
