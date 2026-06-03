"""Permission policy — part of the agent's portable harness.

Decides which tool calls auto-run vs. need human ✅/❌ approval. The approval
*mechanism* lives in runtime/permissions.py; the *policy* (what's safe) lives here
so it travels with the agent regardless of workspace. Future per-path / per-workspace
write rules belong here too.
"""

# Tools with no side effects on the machine or the outside world: auto-allow.
# Everything else (Write, Edit, Bash, …) is gated behind a human in Slack.
SAFE_TOOLS = {
    "Read", "Glob", "Grep", "NotebookRead",
    "WebSearch", "WebFetch", "TodoWrite", "Skill", "Agent",
    # The agent's read-only Slack readers (in-process MCP tools).
    "mcp__slack__read_channel", "mcp__slack__read_thread",
}


def is_safe(tool_name: str, input_data: dict) -> bool:
    """True if the tool may run without human approval."""
    return tool_name in SAFE_TOOLS
