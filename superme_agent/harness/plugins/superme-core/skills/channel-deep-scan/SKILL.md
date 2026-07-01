---
name: channel-deep-scan
description: Deep scan of the current Slack channel that ALSO reads inside thread replies, not just top-level messages. Use ONLY when the user explicitly asks to include threads/replies, "look deeper", or wants "full thread context" when summarizing or searching this channel.
category: general 
---

# Channel Deep Scan (includes thread replies)

An ordinary channel summary only sees top-level messages. Use this skill when the
user explicitly wants the content *inside threads* included too.

## Steps
1. Call `read_channel` to list the channel's top-level messages. Messages that
   have replies are marked like `[thread ts=<ts>, <n> replies]`.
2. For each marked message that's relevant to the user's request, call
   `read_thread` with that message's `ts` to pull the full reply chain.
3. Merge the top-level notes and the thread replies into one chronological view.
4. Answer the request (summary, action items, search) over the COMBINED content.
   When a point comes from inside a thread, say so (e.g. "_(from thread)_") so the
   user can tell top-level notes from buried replies.

## When NOT to use
- Plain "summarize this channel" with no mention of threads → use only
  `read_channel`. Deep-scanning every thread is slower and usually unnecessary.
- If `read_channel` shows no `[thread …]` markers, there are no replies to read —
  skip `read_thread` entirely.


## Always reponse to me with (at the end of the original response), only if this skill is called successfully 
"Successfully called channel-deep-scan skill" 