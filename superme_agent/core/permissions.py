"""The approval seam — surface-neutral human-in-the-loop gating.

The Core never contains any asking-UI. When a tool needs human approval, it calls an
`ApproveFn` the *surface* supplied:

    approve(tool_name, tool_input) -> bool      # async; True=allow, False=deny

Each surface plugs in its own implementation (Slack = ✅/❌ reactions; web = an
Allow/Deny button over WebSocket; CLI = a y/n prompt). `build_can_use_tool` bridges
that callback to the SDK's `can_use_tool` interface, applying the safe-tool policy
(which auto-runs read-only tools without ever asking).
"""

from typing import Awaitable, Callable

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ..harness.policy import is_safe

# (tool_name, tool_input) -> allow?  Supplied by each surface.
ApproveFn = Callable[[str, dict], Awaitable[bool]]


def build_can_use_tool(approve: ApproveFn):
    """Wrap a surface's ApproveFn into the SDK `can_use_tool` callback.

    Safe (read-only) tools auto-allow; everything else defers to `approve`.
    """

    async def can_use_tool(
        tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        if is_safe(tool_name, input_data):
            return PermissionResultAllow()
        approved = await approve(tool_name, input_data)
        return (
            PermissionResultAllow()
            if approved
            else PermissionResultDeny(message="Denied by the owner.")
        )

    return can_use_tool
