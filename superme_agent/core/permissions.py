"""The approval seam — surface-neutral human-in-the-loop gating.

The Core never contains any asking-UI. When a tool needs human approval, it calls an
`ApproveFn` the *surface* supplied:

    approve(tool_name, tool_input) -> bool      # async; True=allow, False=deny

Each surface plugs in its own implementation (Slack = ✅/❌ reactions; web = an
Allow/Deny button over WebSocket; CLI = a y/n prompt). `build_can_use_tool` bridges
that callback to the SDK's `can_use_tool` interface, applying the safe-tool policy
(which auto-runs read-only tools without ever asking).
"""

import logging
from pathlib import Path
from typing import Awaitable, Callable

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from ..harness.policy import is_safe

log = logging.getLogger("superme-agent")

# (tool_name, tool_input) -> allow?  Supplied by each surface.
ApproveFn = Callable[[str, dict], Awaitable[bool]]

# Tools that write to the filesystem (reads are covered by the safe-tool policy).
_WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


async def deny_all(tool_name: str, tool_input: dict) -> bool:
    """An ApproveFn that denies everything — the fallback for a headless run with no human."""
    return False


def scoped_writes_approve(allowed_dir: Path, fallback: ApproveFn) -> ApproveFn:
    """An ApproveFn that auto-allows writes *inside* `allowed_dir`, deferring everything
    else to `fallback`. Reads are already auto-allowed upstream (safe-tool policy), so this
    makes an agent autonomous within one folder while keeping a gate on everything outside it
    — e.g. a planning agent can freely write its own work-item but not real source code.

    With `fallback=deny_all` this is a hard sandbox (headless: no human to ask); with
    `fallback=<surface approve>` the outside-writes still prompt the human (interactive)."""
    allowed = allowed_dir.resolve()

    async def approve(tool_name: str, tool_input: dict) -> bool:
        if tool_name in _WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("path")
            if path:
                try:
                    target = Path(path).resolve()
                    if target == allowed or allowed in target.parents:
                        return True
                except (OSError, ValueError):
                    pass
        return await fallback(tool_name, tool_input)

    return approve


def learning_write_approve(workspace: Path) -> ApproveFn:
    """Policy for a headless learning WRITE run (the `forge` sub-agent). Forge needs two non-safe
    tools with no human to ask: `Bash` (to run the forge_kit lint + behavioural eval) and `Write`
    (to draft the artifact into its scratch workspace). Auto-allow Bash, and writes inside
    `workspace`; deny everything else (no edits to real source, no writes outside the scratch dir).

    This is safe to run unattended because the run is hermetic and disposable: it operates on our
    own toolkit, the proposal text is the only input, the transcript is discarded, and the workspace
    is removed afterwards. The actual disk publish stays owner-gated downstream (gate 2)."""
    ws = workspace.resolve()

    async def approve(tool_name: str, tool_input: dict) -> bool:
        if tool_name == "Bash":
            return True
        if tool_name in _WRITE_TOOLS:
            path = tool_input.get("file_path") or tool_input.get("path")
            if path:
                try:
                    target = Path(path).resolve()
                    if target == ws or ws in target.parents:
                        return True
                except (OSError, ValueError):
                    pass
        return False

    return approve


_SKILL_TOOLS = {"Skill", "SlashCommand"}


def _invoked_skill_names(tool_name: str, input_data: dict) -> list[str]:
    """Best-effort: the skill/command identifiers a Skill/SlashCommand call targets. We look at every
    string value, drop a leading `/` and any args, and also yield the bare name after a `<ns>:` —
    so both `superme-dev:forge-skill` and `forge-skill` can be matched against the blocked set."""
    if tool_name not in _SKILL_TOOLS:
        return []
    out: list[str] = []
    for v in input_data.values():
        if isinstance(v, str) and v.strip():
            tok = v.strip().lstrip("/").split()[0] if v.strip().lstrip("/").split() else ""
            if tok:
                out.append(tok)
                if ":" in tok:
                    out.append(tok.split(":", 1)[1])
    return out


def build_can_use_tool(approve: ApproveFn, *, blocked_skills: set[str] | None = None):
    """Wrap a surface's ApproveFn into the SDK `can_use_tool` callback.

    Safe (read-only) tools auto-allow; everything else defers to `approve`. `blocked_skills` (the
    `access: silent` set) are denied OUTRIGHT before the safe-tool check — even though `Skill` is a
    safe tool — so internal machinery (forge-*) can't be invoked from a user-facing turn. The owning
    sub-run passes no `blocked_skills`, so it can still invoke them.
    """

    async def can_use_tool(
        tool_name: str, input_data: dict, context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        if blocked_skills and tool_name in _SKILL_TOOLS:
            if any(n in blocked_skills for n in _invoked_skill_names(tool_name, input_data)):
                log.info("blocked silent skill via %s: %s", tool_name, input_data)
                return PermissionResultDeny(
                    message="This is an internal SuperMe skill — it runs only inside the learning "
                            "pipeline, not from chat.")
        if is_safe(tool_name, input_data):
            return PermissionResultAllow()
        approved = await approve(tool_name, input_data)
        log.info("approval: %s -> %s", tool_name, "ALLOW" if approved else "DENY")
        return (
            PermissionResultAllow()
            if approved
            else PermissionResultDeny(message="Denied by the owner.")
        )

    return can_use_tool
