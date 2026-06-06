"""Slash-command dispatch — the "REPL slash command callability" layer.

A small, extensible registry so we can keep adding REPL-style slash commands
(`/compact`, …) without touching the mention handler. Each command is a name +
async handler + one-line help. A handler receives a `CommandContext` carrying the
Slack I/O handles, the channel/thread/user, and the assistant — everything it
needs. Adding a command = write a handler + one `reg.register(...)` line in
`build_registry()`.

Two parts, deliberately separated:
  - FRAMEWORK  — CommandContext, Command, CommandRegistry, parse_slash (stable).
  - DEFINITIONS — the handlers + build_registry() at the bottom (grows over time).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from . import models

log = logging.getLogger("superme-agent")


# --- framework ---------------------------------------------------------------
@dataclass
class CommandContext:
    """Everything a command handler might need to do its job."""

    args: str            # text after the command name, trimmed ("" if none)
    channel: str
    thread_ts: str
    user: str            # the requester (e.g. for approval ownership)
    say: Callable        # Bolt say()
    client: object       # Bolt web client (chat_update, etc.)
    assistant: object    # runtime.agent.Assistant


# A handler returns a reply string to post, or None if it delivered output itself.
Handler = Callable[[CommandContext], Awaitable[Optional[str]]]


@dataclass
class Command:
    name: str            # canonical name, no leading slash
    handler: Handler
    help: str = ""


class CommandRegistry:
    """Name -> Command, with lookup and listing."""

    def __init__(self) -> None:
        self._cmds: dict[str, Command] = {}

    def register(self, name: str, handler: Handler, help: str = "") -> None:
        key = name.lstrip("/").lower()
        self._cmds[key] = Command(key, handler, help)

    def get(self, name: str) -> Optional[Command]:
        return self._cmds.get(name.lstrip("/").lower())

    def all(self) -> list[Command]:
        return [self._cmds[k] for k in sorted(self._cmds)]


def parse_slash(prompt: str) -> Optional[tuple[str, str]]:
    """Split a leading-slash prompt into (name, args). None if not a slash command.

    `/compact`        -> ("compact", "")
    `/foo a b`        -> ("foo", "a b")
    (anything else)   -> None
    """
    if not prompt.startswith("/"):
        return None
    parts = prompt[1:].split(maxsplit=1)
    if not parts:
        return None
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


# --- definitions (the handlers + the registry they live in) ------------------
async def _cmd_compact(ctx: CommandContext) -> Optional[str]:
    """`/compact` — squeeze this thread's context (forwarded to the REPL command)."""
    placeholder = await ctx.say(text="🗜️ _Compacting this thread…_", thread_ts=ctx.thread_ts)
    msg = await ctx.assistant.compact(
        ctx.channel, ctx.thread_ts, ctx.user, ctx.say, ctx.client
    )
    try:
        await ctx.client.chat_update(channel=ctx.channel, ts=placeholder["ts"], text=msg)
    except Exception:
        await ctx.say(text=msg, thread_ts=ctx.thread_ts)
    return None  # delivered already


async def _cmd_model(ctx: CommandContext) -> Optional[str]:
    """`/model [haiku|sonnet|opus|reset]` — show or set this channel's model."""
    arg = ctx.args.strip().lower()
    opts = ", ".join(f"`{m}`" for m in models.allowed())
    if not arg or arg in ("show", "status", "?"):
        cur = models.get(ctx.channel)
        now = f"*{cur}*" if cur else "the *default* model"
        return f"🧠 This channel uses {now}.\nSet with `@me /model <name>` ({opts}) or `/model reset`."
    if arg in ("reset", "default", "clear"):
        return models.clear(ctx.channel)
    _, msg = models.set_model(ctx.channel, arg)
    return msg


def build_registry() -> CommandRegistry:
    """Assemble the slash-command registry. Add new commands here."""
    reg = CommandRegistry()
    reg.register("compact", _cmd_compact, "Compact this thread's context to free up room.")
    reg.register("model", _cmd_model, "Show or set this channel's model (haiku/sonnet/opus).")

    async def _cmd_help(ctx: CommandContext) -> Optional[str]:
        lines = ["*Slash commands*"]
        lines += [f"• `/{c.name}` — {c.help}" for c in reg.all()]
        return "\n".join(lines)

    reg.register("help", _cmd_help, "List available slash commands.")
    return reg
