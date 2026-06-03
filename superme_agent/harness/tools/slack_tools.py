"""In-process tools that give the AGENT scoped access to the Slack workspace.

The outer app already holds a Slack Web client (Bolt). These factories expose
narrow, read-only slices of it to the agent as SDK MCP tools, so the agent can
pull context on demand instead of us pre-stuffing every prompt.

Two tools, both bound to the channel where the bot was mentioned:
  - read_channel : top-level messages (the user's notes). The default, cheap view.
  - read_thread  : the full reply chain under one message. Used only for a
                   deliberate "deep scan" (see the channel-deep-scan skill).
"""

import re

from claude_agent_sdk import tool, create_sdk_mcp_server

# Slack message permalinks: .../archives/<CHANNEL>/p<10 digits><6 digits>
# where the p-digits are the ts (decimal point 6 from the right). Links to a reply
# also carry ?thread_ts=<parent ts>, which is the better ts to open the thread.
_PERMALINK_RE = re.compile(r"/archives/(C[A-Z0-9]+)/p(\d{10})(\d{6})")
_THREAD_TS_RE = re.compile(r"[?&]thread_ts=(\d{10}\.\d{6})")


def _parse_ref(raw: str, default_channel: str) -> tuple[str, str]:
    """Parse a thread reference into (channel, ts).

    Accepts a raw ts ('1700000000.123456') → uses the current channel; or a pasted
    Slack message link → uses the channel AND message encoded in the link (so you
    can refer to a thread in a DIFFERENT channel the bot is in).
    """
    raw = raw.strip()
    m = _PERMALINK_RE.search(raw)
    if not m:
        return default_channel, raw
    tq = _THREAD_TS_RE.search(raw)
    ts = tq.group(1) if tq else f"{m.group(2)}.{m.group(3)}"
    return m.group(1), ts


def _clean_lines(messages, *, reverse: bool) -> list[str]:
    """Turn raw Slack messages into '- text' lines, dropping noise + bot posts."""
    ordered = reversed(messages) if reverse else messages
    lines = []
    for m in ordered:
        if m.get("subtype") or m.get("bot_id"):  # skip joins + the bot's own posts
            continue
        text = (m.get("text") or "").strip()
        if text:
            lines.append(text)
    return lines


def make_slack_mcp_server(client, channel: str):
    """Build the `slack` MCP server (read_channel + read_thread) for one channel."""

    @tool(
        "read_channel",
        "Read recent top-level messages the user posted in the CURRENT Slack "
        "channel. Use this whenever the user asks about, summarizes, or refers to "
        "notes/messages in 'this channel'. Returns messages oldest-first. A "
        "message that has replies is marked '[thread ts=<ts>, <n> replies]'; pass "
        "that ts to read_thread to read inside it.",
        {"limit": int},
    )
    async def read_channel(args: dict) -> dict:
        limit = max(1, min(int(args.get("limit") or 100), 200))
        try:
            resp = await client.conversations_history(channel=channel, limit=limit)
        except Exception as e:  # missing scope, not_in_channel, rate limit, …
            return {
                "content": [{"type": "text", "text": f"Could not read channel: {e}"}],
                "is_error": True,
            }

        lines = []
        for m in reversed(resp.get("messages", [])):  # API returns newest-first
            if m.get("subtype") or m.get("bot_id"):  # skip joins + the bot's own posts
                continue
            text = (m.get("text") or "").strip()
            if not text:
                continue
            replies = m.get("reply_count", 0)
            if replies:
                lines.append(f"- {text}  [thread ts={m.get('ts')}, {replies} replies]")
            else:
                lines.append(f"- {text}")

        body = "\n".join(lines) if lines else "(no user messages found in this channel)"
        return {"content": [{"type": "text", "text": body}]}

    @tool(
        "read_thread",
        "Read the full reply chain under ONE message. Pass `ts` as either a message "
        "timestamp (from a '[thread ts=...]' marker in read_channel's output) OR a "
        "Slack message permalink the user pasted (⋯ → Copy link). A permalink may "
        "point to a thread in a DIFFERENT channel — that works as long as the bot is "
        "a member there. Use to summarize/answer about a specific thread, not for an "
        "ordinary channel summary.",
        {"ts": str},
    )
    async def read_thread(args: dict) -> dict:
        target_channel, ts = _parse_ref(str(args.get("ts") or ""), channel)
        if not ts:
            return {
                "content": [{"type": "text", "text": "read_thread needs a message `ts` or a Slack link."}],
                "is_error": True,
            }
        try:
            resp = await client.conversations_replies(channel=target_channel, ts=ts, limit=200)
        except Exception as e:
            return {
                "content": [{"type": "text", "text":
                    f"Could not read thread in {target_channel}: {e}. "
                    "(If it's another channel, make sure the bot is invited there.)"}],
                "is_error": True,
            }

        # conversations_replies returns the parent first, then replies oldest-first.
        lines = _clean_lines(resp.get("messages", []), reverse=False)
        body = "\n".join(f"- {t}" for t in lines) if lines else "(no messages in this thread)"
        return {"content": [{"type": "text", "text": body}]}

    return create_sdk_mcp_server(
        name="slack", version="1.0.0", tools=[read_channel, read_thread]
    )
