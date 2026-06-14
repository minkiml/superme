"""SuperMe Core — the surface-agnostic brain.

Nothing in here knows about Slack, web, or any other surface. The Core exposes two
services and a neutral vocabulary that every surface (adapter) speaks:

  - Context        — who/where a turn runs (global root or a local/project)
  - AgentService   — run a conversational turn, yielding surface-neutral TurnEvents
  - KnowledgeService (Stage C) — read/write/list/inject knowledge files
  - TurnEvent      — TextDelta | Status | Result
  - ApproveFn      — async (tool_name, tool_input) -> bool, supplied by the surface

Surfaces translate their native I/O (Slack events, HTTP/WebSocket) into these calls.
"""

from .context import Context
from .events import TextDelta, Status, Result, TurnEvent
from .permissions import ApproveFn, build_can_use_tool
from .agent_service import AgentService

__all__ = [
    "Context",
    "TextDelta",
    "Status",
    "Result",
    "TurnEvent",
    "ApproveFn",
    "build_can_use_tool",
    "AgentService",
]
