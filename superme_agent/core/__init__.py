"""SuperMe Core — the surface-agnostic brain.

Exposes Context (who and where a turn runs), AgentService (runs turns, yields TurnEvents),
KnowledgeService, and ApproveFn (per-surface tool approval). Nothing here knows a surface.
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
