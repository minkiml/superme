"""The agent core (Layer 1 glue).

Composes ClaudeAgentOptions = the portable harness (always) + the resolved
workspace (per channel), then runs the SDK query loop.

Harness, loaded cwd-INDEPENDENTLY so the agent is "itself" in any workspace:
  - persona  -> system_prompt append (harness/persona.md)
  - skills + subagents + hooks + static MCP -> local plugin (harness/plugin)
  - in-process Slack tools -> mcp_servers (harness/tools)
  - permission policy -> can_use_tool (harness/policy)

Workspace, per channel (workspaces.py): cwd + setting_sources(project/local) load
that repo's own CLAUDE.md/skills, plus optional persona_append / extra MCP.
"""

import re
import logging

from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    AssistantMessage,
    ResultMessage,
    SystemMessage,
)

from .config import PERSONA_FILE, PLUGIN_DIR
from .permissions import PermissionManager
from .sessions import SessionStore
from . import workspaces
from . import models
from ..harness.tools.slack_tools import make_slack_mcp_server

log = logging.getLogger("superme-agent")

# The portable persona, loaded once from the harness.
PERSONA = PERSONA_FILE.read_text()


async def _stream_prompt(text: str):
    """Wrap one user turn as the streaming-input message the SDK expects.

    A `can_use_tool` callback forces streaming mode, so the prompt must be an
    AsyncIterable of message dicts rather than a plain string.
    """
    yield {
        "type": "user",
        "message": {"role": "user", "content": text},
        "parent_tool_use_id": None,
    }


def _format_model(resolved: str | None) -> str:
    """'claude-opus-4-7' / 'claude-haiku-4-5-20251001' -> 'opus 4.7' / 'haiku 4.5'.

    Falls back to the raw id if it doesn't match the expected shape.
    """
    if not resolved:
        return ""
    m = re.match(r"claude-([a-z]+)-(\d+)-(\d+)", resolved)
    return f"{m.group(1)} {m.group(2)}.{m.group(3)}" if m else resolved


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


def _friendly_status(tool_name: str, tool_input: dict) -> str:
    """A short Slack-mrkdwn status line for a tool the agent is about to run."""
    inp = tool_input or {}
    if tool_name in ("Read", "Glob", "Grep", "NotebookRead"):
        return "🔍 _reading files…_"
    if tool_name in ("Edit", "Write", "NotebookEdit"):
        f = (inp.get("file_path") or "").rsplit("/", 1)[-1]
        return f"✏️ _editing {f}…_" if f else "✏️ _editing files…_"
    if tool_name == "Bash":
        return "💻 _running a command…_"
    if tool_name in ("WebSearch", "WebFetch"):
        return "🌐 _searching the web…_"
    if tool_name == "mcp__slack__read_channel":
        return "💬 _reading the channel…_"
    if tool_name == "mcp__slack__read_thread":
        return "🗨️ _reading a thread…_"
    if tool_name == "Skill":
        return "🧩 _using a skill…_"
    if tool_name == "Agent":
        return "🤖 _delegating to a subagent…_"
    if tool_name == "TodoWrite":
        return "🗒️ _planning…_"
    return f"🔧 _{tool_name}…_"


class Assistant:
    """Wraps the Claude Agent SDK as the SuperMe host agent."""

    def __init__(self, sessions: SessionStore, permissions: PermissionManager):
        self._sessions = sessions
        self._permissions = permissions

    def _build_options(self, say, client, channel, thread_ts, user, resume, ws) -> ClaudeAgentOptions:
        append = PERSONA + (f"\n\n{ws.persona_append}" if ws.persona_append else "")
        return ClaudeAgentOptions(
            cwd=str(ws.cwd),                       # the workspace (Layer 3)
            resume=resume,                          # continuous per-thread session
            model=models.get(channel),              # per-channel override (None = default)
            system_prompt={"type": "preset", "preset": "claude_code", "append": append},
            # Workspace harness only; the agent's own harness comes from the plugin
            # below, so it loads regardless of cwd (and not from ~/.claude).
            setting_sources=["project", "local"],
            plugins=[{"type": "local", "path": str(PLUGIN_DIR)}],
            skills="all",
            mcp_servers={"slack": make_slack_mcp_server(client, channel)},
            permission_mode="default",
            can_use_tool=self._permissions.make_callback(say, client, thread_ts, user),
        )

    async def _run_once(self, prompt, say, client, channel, thread_ts, user, resume, on_status, ws):
        """Run one SDK turn. Returns (reply_text, resolved_model_id, context_pct)."""
        final = ""
        resolved_model = None
        context = None
        options = self._build_options(say, client, channel, thread_ts, user, resume, ws)
        async for message in query(prompt=_stream_prompt(prompt), options=options):
            if isinstance(message, SystemMessage):
                # The init system message reports the concrete model the CLI resolved.
                if getattr(message, "subtype", "") == "init":
                    resolved_model = (getattr(message, "data", None) or {}).get("model")
            elif isinstance(message, AssistantMessage):
                for block in message.content:
                    if hasattr(block, "text"):
                        log.info("assistant: %s", block.text[:200])
                    elif on_status and hasattr(block, "name") and hasattr(block, "input"):
                        # A tool-use block — surface what it's doing as live status.
                        await on_status(_friendly_status(block.name, block.input))
            elif isinstance(message, ResultMessage):
                if getattr(message, "session_id", None):
                    self._sessions.remember(thread_ts, message.session_id)
                context = _context_usage(message.usage, message.model_usage, resolved_model)
                final = (
                    message.result
                    if message.subtype == "success"
                    else "Sorry — I ran into an error handling that request."
                )
        return (final or "I didn't produce a response."), resolved_model, context

    async def compact(self, channel, thread_ts, user, say, client) -> str:
        """Forward the REPL `/compact` command to this thread's session.

        The underlying CLI intercepts `/compact` as a local command (it writes a
        compact_boundary into the transcript rather than answering as the model),
        so we just route it through a normal run and report our own confirmation.
        """
        resume = self._sessions.get(thread_ts)
        if not resume:
            return "Nothing to compact yet — this thread has no saved session."
        ws = workspaces.resolve_for_run(channel)
        try:
            await self._run_once(
                "/compact", say, client, channel, thread_ts, user, resume, None, ws
            )  # return value (reply, model) unused here
        except Exception:
            log.exception("compact failed for thread %s", thread_ts)
            return "Sorry — compaction failed. Check the logs."
        return "🗜️ Compacted this thread's context — we can keep going with more room."

    async def run(self, prompt, say, client, channel, thread_ts, user, on_status=None) -> str:
        """Run one turn. If resuming a stale session fails (e.g. the transcript was
        created under a different cwd), drop it and retry as a fresh conversation.

        `on_status(text)` (optional) is awaited with a short status line each time a
        tool runs, for a live "what it's doing now" indicator in Slack.
        """
        # Resolve the channel's workspace; the first run LOCKS the channel to it,
        # so the workspace never changes mid-stream for any thread in the channel.
        ws = workspaces.resolve_for_run(channel)
        resume = self._sessions.get(thread_ts)
        try:
            final, resolved_model, context = await self._run_once(prompt, say, client, channel, thread_ts, user, resume, on_status, ws)
        except Exception:
            if not resume:
                raise  # genuine failure, not a stale-resume problem
            log.warning("resume failed for thread %s; retrying as a fresh session", thread_ts)
            self._sessions.forget(thread_ts)
            final, resolved_model, context = await self._run_once(prompt, say, client, channel, thread_ts, user, None, on_status, ws)

        # Subtle footer: workspace + the concrete model used (e.g. "opus 4.7") + context fill.
        tags = [f"⌂ *Workspace*: {ws.label}"]
        model_label = _format_model(resolved_model)
        if model_label:
            tags.append(f"*Model*: {model_label}")
        if context:
            pct, window = context
            tags.append(f"*Context ({window // 1000}k)*: {pct}%")
        final = f"{final}\n\n_{'  ·  '.join(tags)}_"
        return final
