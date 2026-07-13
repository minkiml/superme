"""The Slack adapter — drives the surface-agnostic Core for Slack.

The thinking lives in core/ (AgentService). This module is Slack glue: it resolves
the channel's workspace into a Context, supplies a Slack ApproveFn and the in-process
Slack reader tools, consumes the Core's TurnEvents, and renders them for Slack
(live status, the final reply, the workspace/model/context footer). Session storage
(thread → session_id) and the model override are Slack-surface state too.

Keeping `Assistant.run()` / `compact()` signatures stable means slack_app.py and
commands.py are unaffected by the Core extraction.
"""

import re
import logging

from ..core import AgentService, Context, Status, Result
from .permissions import PermissionManager
from .sessions import SessionStore
from . import workspaces
from . import models
from ..harness.tools.slack_tools import make_slack_mcp_server

log = logging.getLogger("superme-agent")


def _format_model(resolved: str | None) -> str:
    """Canonical 'Name Version' label: 'claude-opus-4-8' -> 'Opus 4.8',
    'claude-haiku-4-5-20251001' -> 'Haiku 4.5', 'claude-sonnet-5' -> 'Sonnet 5'.

    Falls back to the raw id if it doesn't match the expected shape.
    """
    if not resolved:
        return ""
    m = re.match(r"claude-([a-z]+)-(\d+)(?:-(\d+))?", resolved)
    if not m:
        return resolved
    name = m.group(1).capitalize()
    return f"{name} {m.group(2)}.{m.group(3)}" if m.group(3) else f"{name} {m.group(2)}"


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


# Slack-surface formatting + routing. Lives in the Slack adapter (not the shared
# persona) so it applies ONLY when the owner is reaching SuperMe through Slack.
SLACK_PERSONA_NOTE = (
    "You are reaching the owner through **Slack**, a chat surface: keep replies tight; "
    "format with Slack mrkdwn (*bold*, _italic_, `code`, > quote). To read or summarize "
    "the current channel use the `read_channel` tool; only go into thread replies when "
    "asked (the `channel-deep-scan` skill)."
)


def _context_from_workspace(ws) -> Context:
    """Map a resolved Slack workspace to a Core Context (global root vs local)."""
    layer = "global" if ws.name == workspaces.default_workspace() else "local"
    persona_append = SLACK_PERSONA_NOTE
    if ws.persona_append:
        persona_append += f"\n\n{ws.persona_append}"
    return Context(
        layer=layer,
        id=ws.name,
        cwd=ws.cwd,
        knowledge_root=None,            # wired in Stage C (superme-global-knowledge/)
        persona_append=persona_append,
        extra_mcp=ws.extra_mcp,
        label=ws.label,
    )


class Assistant:
    """Slack-facing host agent: a thin adapter over the Core's AgentService."""

    def __init__(self, sessions: SessionStore, permissions: PermissionManager):
        self._sessions = sessions
        self._permissions = permissions
        self._agent = AgentService()      # loads the portable persona once

    async def _consume(self, ctx, prompt, resume, model, approve, mcp, on_status) -> Result:
        """Run one Core turn, rendering Status as live Slack status; return the Result."""
        result: Result | None = None
        async for ev in self._agent.run_turn(
            ctx, prompt, resume=resume, model=model, approve=approve,
            extra_mcp_servers=mcp, enforce_silent=True,   # user-facing chat
        ):
            if isinstance(ev, Status):
                if on_status:
                    await on_status(_friendly_status(ev.tool_name, ev.tool_input))
            elif isinstance(ev, Result):
                result = ev
            # TextDelta is ignored for Slack — we post the final Result.text.
        return result or Result(text="I didn't produce a response.")

    async def compact(self, channel, thread_ts, user, say, client) -> str:
        """Forward the REPL `/compact` command to this thread's session.

        The underlying CLI intercepts `/compact` as a local command (it writes a
        compact_boundary into the transcript rather than answering as the model),
        so we just route it through a normal turn and report our own confirmation.
        """
        resume = self._sessions.get(thread_ts)
        if not resume:
            return "Nothing to compact yet — this thread has no saved session."
        ctx = _context_from_workspace(workspaces.resolve_for_run(channel))
        approve = self._permissions.make_approver(say, client, thread_ts, user)
        mcp = {"slack": make_slack_mcp_server(client, channel)}
        try:
            await self._consume(
                ctx, "/compact", resume, models.get(channel), approve, mcp, None
            )
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
        ctx = _context_from_workspace(ws)
        approve = self._permissions.make_approver(say, client, thread_ts, user)
        mcp = {"slack": make_slack_mcp_server(client, channel)}
        model = models.get(channel)
        resume = self._sessions.get(thread_ts)

        try:
            result = await self._consume(ctx, prompt, resume, model, approve, mcp, on_status)
        except Exception:
            if not resume:
                raise  # genuine failure, not a stale-resume problem
            log.warning("resume failed for thread %s; retrying as a fresh session", thread_ts)
            self._sessions.delete(ctx, thread_ts, cause="deleted")  # drop the broken resume; trace preserved
            result = await self._consume(ctx, prompt, None, model, approve, mcp, on_status)

        if result.session_id:
            self._sessions.remember(thread_ts, result.session_id, ctx.cwd)

        # Subtle footer: workspace + the concrete model used (e.g. "opus 4.7") + context fill.
        tags = [f"⌂ *Workspace*: {ws.label}"]
        model_label = _format_model(result.model)
        if model_label:
            tags.append(f"*Model*: {model_label}")
        if result.ctx_pct is not None and result.context_window:
            tags.append(f"*Context ({result.context_window // 1000}k)*: {result.ctx_pct}%")
        return f"{result.text}\n\n_{'  ·  '.join(tags)}_"
