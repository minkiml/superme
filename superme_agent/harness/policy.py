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
    # The dev agent's read-only activity-log reader (PRD §4.9).
    "mcp__dev__dev_log",
    # Capture SWEEP (WI-8) — the `capture` sub-agent's pen; files a candidate row from a swept
    # conversation slice. Nothing is applied here (the owner gate is downstream).
    "mcp__dev__file_candidate",
    # Memory PROCESSING (PRD §4.10.2) — read the candidate pool, file consolidated proposals.
    # Still pre-gate: a proposal is a reversible draft awaiting the owner's accept/reject; the
    # apply step (which writes memory/ files) is what's actually gated, downstream.
    "mcp__dev__review_candidates",
    "mcp__dev__propose_memory",
    # WRITE phase (WI-8 §Phase 3) — the `write` sub-agent's pen; stages the authored artifact into
    # the proposal row (→ drafted). Still pre-gate: staging writes only to the DB, never to disk;
    # the disk write (publish) is the owner-gated step downstream.
    "mcp__dev__stage_artifact",
}


def is_safe(tool_name: str, input_data: dict) -> bool:
    """True if the tool may run without human approval."""
    return tool_name in SAFE_TOOLS
