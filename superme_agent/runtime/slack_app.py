"""Slack wiring: create the Bolt app and route @mentions to the assistant.

This is the I/O layer — receive a mention, hand the text to the agent, post the
reply back into the same thread. All the thinking happens in agent.py.
"""

import re
import logging

from slack_bolt.async_app import AsyncApp

from .config import SLACK_BOT_TOKEN
from .agent import Assistant
from . import workspaces

log = logging.getLogger("superme-agent")

# Strips the "<@U123>" mention tags out of the message text.
MENTION_RE = re.compile(r"<@[A-Z0-9]+>")

# Control command: "@bot workspace [use <name> | reset | <name>]"
WORKSPACE_RE = re.compile(r"^workspace\b\s*(.*)$", re.IGNORECASE)

# Slack truncates very long messages in the UI; chunk well under that so replies
# stay fully readable. We split on line boundaries to avoid cutting mid-sentence.
MAX_SLACK_CHARS = 3500


def _chunk(text: str, size: int = MAX_SLACK_CHARS) -> list[str]:
    """Split text into <=size pieces, preferring line boundaries."""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        # A single line longer than `size` must be hard-split.
        while len(line) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size and current:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks or [""]


async def _deliver(client, channel, placeholder_ts, thread_ts, say, text: str) -> None:
    """Replace the '⏳ Thinking…' placeholder with the reply (chunked if long)."""
    chunks = _chunk(text)
    await client.chat_update(channel=channel, ts=placeholder_ts, text=chunks[0])
    for chunk in chunks[1:]:
        await say(text=chunk, thread_ts=thread_ts)


async def _handle_workspace_command(arg: str, channel: str, say, thread_ts) -> None:
    """`@bot workspace …` — link/unlink/show this channel's workspace (no restart)."""
    arg = arg.strip()
    low = arg.lower()
    if low in ("", "show", "status", "?"):
        cur = workspaces.current(channel)
        avail = ", ".join(f"`{n}`" for n in workspaces.known_workspaces())
        pin = workspaces.pinned(thread_ts)
        lines = []
        if pin and pin != cur:
            lines.append(f"This *thread* is pinned to *{pin}* (it keeps its workspace for life).")
        lines.append(f"New threads in this channel → *{cur}* workspace.")
        lines.append(f"Defined: {avail}")
        lines.append("`@me workspace use <name>` to switch the channel default, or `… reset`.")
        await say(thread_ts=thread_ts, text="\n".join(lines))
        return
    if low in ("reset", "clear", "default", "unlink"):
        await say(text=workspaces.unlink(channel), thread_ts=thread_ts)
        return
    name = arg[4:].strip() if low.startswith("use ") else arg
    _, msg = workspaces.link(channel, name)
    await say(text=msg, thread_ts=thread_ts)


def create_app() -> AsyncApp:
    return AsyncApp(token=SLACK_BOT_TOKEN)


def register_mention_handler(app: AsyncApp, assistant: Assistant) -> None:
    """Route every `app_mention` to the assistant, replying in-thread."""

    @app.event("app_mention")
    async def handle_mention(event, say, client):
        prompt = MENTION_RE.sub("", event["text"]).strip()
        # New top-level mention -> its own ts (fresh thread); reply -> parent ts.
        thread_ts = event.get("thread_ts", event["ts"])
        channel = event["channel"]
        user = event["user"]  # the requester — only they can approve via reaction
        if not prompt:
            await say(text="Ask me something.", thread_ts=thread_ts)
            return

        # Control command: manage this channel's workspace link, no agent run.
        ws_cmd = WORKSPACE_RE.match(prompt)
        if ws_cmd:
            await _handle_workspace_command(ws_cmd.group(1), channel, say, thread_ts)
            return

        log.info("prompt: %s", prompt)
        # Immediate feedback so the user knows it's working, not stalled.
        placeholder = await say(text="⏳ _Thinking…_", thread_ts=thread_ts)

        async def on_status(text: str) -> None:
            # Live "what it's doing now" — best-effort; never fail the turn on it.
            try:
                await client.chat_update(
                    channel=channel, ts=placeholder["ts"], text=text
                )
            except Exception:
                pass

        try:
            reply = await assistant.run(
                prompt, say, client, channel, thread_ts, user, on_status=on_status
            )
        except Exception:
            # Without this, an SDK/Slack error would die silently and the user
            # would just see nothing. Surface it instead.
            log.exception("agent run failed")
            reply = "Sorry — something went wrong handling that. Check the logs."
        await _deliver(client, channel, placeholder["ts"], thread_ts, say, reply)
