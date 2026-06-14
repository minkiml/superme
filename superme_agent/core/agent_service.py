"""AgentService — run one conversational turn, surface-agnostically.

This is the extracted brain. It composes ClaudeAgentOptions from the in-code harness
(persona + local plugin) plus the per-run Context (cwd, persona_append, workspace
harness via setting_sources), runs the SDK query loop, and yields surface-neutral
TurnEvents. It knows nothing about Slack, web, sessions storage, or model policy —
those are the surface's job and arrive as plain parameters (resume, model, approve,
extra_mcp_servers).
"""

import logging
from typing import AsyncIterator

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
)

from ..runtime.config import PERSONA_FILE, PLUGIN_DIR
from .context import Context
from .events import TextDelta, Status, Result, TurnEvent
from .permissions import ApproveFn, build_can_use_tool

log = logging.getLogger("superme-agent")


def _context_usage(usage: dict | None, model_usage: dict | None, model: str | None):
    """Approximate context-window fill. Returns (percent, window_tokens) or None.

    The last turn's prompt (input + both cache buckets) plus its output ≈ the tokens
    now sitting in the context window. Divide by the model's contextWindow.
    """
    if not usage:
        return None
    used = (
        usage.get("input_tokens", 0)
        + usage.get("cache_creation_input_tokens", 0)
        + usage.get("cache_read_input_tokens", 0)
        + usage.get("output_tokens", 0)
    )
    window = 200_000
    if model_usage:
        entry = model_usage.get(model) if model else None
        entry = entry or next((v for v in model_usage.values() if isinstance(v, dict)), None)
        if entry and entry.get("contextWindow"):
            window = entry["contextWindow"]
    if not used or not window:
        return None
    return round(used / window * 100), window


class AgentService:
    """Runs SDK turns for any surface, emitting TurnEvents."""

    def __init__(self, persona: str | None = None):
        # The portable persona, loaded once from the in-code harness.
        self._persona = persona if persona is not None else PERSONA_FILE.read_text()

    def _context_preamble(self, ctx: Context) -> str:
        """A short note telling the agent which context it's operating in."""
        if ctx.layer == "global":
            where = ("the owner's top-level **global SuperMe** — their cross-domain "
                     "identity and knowledge, not any single project")
        else:
            where = f"a **local / project sub-SuperMe** (`{ctx.id}`)"
        return (
            f"\n\n## Operating context\n"
            f"You are operating in {where}. "
            f"Context: `{ctx.label}` · working directory: `{ctx.cwd}`."
        )

    def _build_options(
        self, ctx: Context, *, resume, model, approve: ApproveFn, extra_mcp_servers
    ) -> ClaudeAgentOptions:
        append = self._persona + self._context_preamble(ctx)
        if ctx.persona_append:
            append += f"\n\n{ctx.persona_append}"
        return ClaudeAgentOptions(
            cwd=str(ctx.cwd),                       # the Context (cwd / workspace)
            resume=resume,                          # continuous session (surface-owned)
            model=model,                            # surface-resolved override (None = default)
            system_prompt={"type": "preset", "preset": "claude_code", "append": append},
            # Workspace harness only; the agent's own harness comes from the plugin
            # below, so it loads regardless of cwd (and not from ~/.claude).
            setting_sources=["project", "local"],
            plugins=[{"type": "local", "path": str(PLUGIN_DIR)}],
            skills="all",
            mcp_servers=extra_mcp_servers or {},    # surface-specific tools (e.g. Slack readers)
            permission_mode="default",
            can_use_tool=build_can_use_tool(approve),
        )

    async def run_turn(
        self,
        ctx: Context,
        prompt: str,
        *,
        resume: str | None = None,
        model: str | None = None,
        approve: ApproveFn,
        extra_mcp_servers: dict | None = None,
    ) -> AsyncIterator[TurnEvent]:
        """Run one turn against `ctx`, yielding TurnEvents.

        Emits TextDelta as assistant text arrives, Status before each tool call, and a
        final Result with the reply + run metadata (model, context fill, session id).
        Raises on a hard SDK failure (the surface decides whether to retry, e.g. after a
        stale resume).

        Uses ClaudeSDKClient (persistent connection) rather than the one-shot query():
        interactive permission callbacks (can_use_tool) need the control channel held
        open for the whole turn, which query() closes once its input stream ends.
        """
        options = self._build_options(
            ctx, resume=resume, model=model, approve=approve,
            extra_mcp_servers=extra_mcp_servers,
        )
        resolved_model = None
        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, SystemMessage):
                    # The init system message reports the concrete model the CLI resolved.
                    if getattr(message, "subtype", "") == "init":
                        resolved_model = (getattr(message, "data", None) or {}).get("model")
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            log.info("assistant: %s", block.text[:200])
                            yield TextDelta(block.text)
                        elif hasattr(block, "name") and hasattr(block, "input"):
                            # A tool-use block — surface its own "what it's doing" indicator.
                            yield Status(block.name, block.input or {})
                elif isinstance(message, ResultMessage):
                    usage = _context_usage(message.usage, message.model_usage, resolved_model)
                    pct, window = usage if usage else (None, None)
                    text = (
                        message.result
                        if message.subtype == "success"
                        else "Sorry — I ran into an error handling that request."
                    )
                    yield Result(
                        text=text or "I didn't produce a response.",
                        model=resolved_model,
                        context_pct=pct,
                        context_window=window,
                        session_id=getattr(message, "session_id", None),
                    )
