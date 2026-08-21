"""SuperMe Core — the surface-agnostic brain.

Nothing here knows about any particular surface. It exposes two services and a neutral
vocabulary every adapter speaks: Context (who and where a turn runs), AgentService (run a turn,
yielding TurnEvents), KnowledgeService, and ApproveFn (per-surface tool approval).
"""

from .context import Context
from .events import Init, TextDelta, Status, ToolResult, Usage, Result, TurnEvent
from .permissions import (
    ApproveFn, build_can_use_tool, scoped_writes_approve, deny_all, learning_write_approve,
    PLAN_READONLY_NUDGE,
    VET_READONLY_NUDGE,
)
from .agent_service import AgentService
from .knowledge_service import KnowledgeService
from .dev_knowledge import DevKnowledgeService
from .dev_store import DevStore
from .sessions import SessionStore
from .commands import CommandLayer

__all__ = [
    "Context",
    "Init",
    "TextDelta",
    "Status",
    "ToolResult",
    "Usage",
    "Result",
    "TurnEvent",
    "ApproveFn",
    "build_can_use_tool",
    "scoped_writes_approve",
    "deny_all",
    "learning_write_approve",
    "PLAN_READONLY_NUDGE",
    "VET_READONLY_NUDGE",
    "AgentService",
    "KnowledgeService",
    "DevKnowledgeService",
    "DevStore",
    "SessionStore",
    "CommandLayer",
]
