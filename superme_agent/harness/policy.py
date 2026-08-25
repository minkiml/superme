"""Permission policy — part of the agent's portable harness.

Decides which tool calls auto-run and which need human approval. The approval MECHANISM lives in
`core/permissions.py`; the policy travels with the agent.
"""

# `Agent` is the SDK's name for the spawn, `Task` the older one; both, so an upgrade cannot uncap.
SUBAGENT_TOOLS = {"Agent", "Task"}

# Nested spawns included; not a concurrency limit. Measured fan-out is 5-8, so more is worth
# interrupting.
MAX_SUBAGENTS = 8

# Tools with no side effect on the machine or the outside world. Everything else is gated.
SAFE_TOOLS = {
    "Read", "Glob", "Grep", "NotebookRead",
    "WebSearch", "WebFetch", "TodoWrite", "Skill", "Agent",
    # Read-only pulls. The frontmatter-first model depends on these being cheap.
    "mcp__superme__read_constitution",
    "mcp__superme__adopt_knowledge_assets",
    # The sanctioned structured endings of a kernel-fired run; a prompt here would park every
    # background run.
    "mcp__run__report_completion",
    "mcp__deputy__submit_gate_verdict",
    # Read-only dev readers, scoped to this repo. A denial makes a run work blind, never stops it.
    "mcp__dev__read_dev_log",
    "mcp__dev__read_inbox",
    "mcp__dev__read_candidates",
    "mcp__dev__read_proposals",
    "mcp__dev__read_run",
    "mcp__dev__read_verification_library",
    "mcp__dev__read_decisions",
    "mcp__dev__read_research_proposals",
    # The one exemption to the general-session guardrail. These can touch nothing but the inbox.
    "mcp__dev__create_inbox_item",
    "mcp__dev__append_inbox_item",
    # Learning-pipeline pens: DB-only and pre-gate. Publishing to disk is the owner-gated step.
    "mcp__dev__file_candidate",
    "mcp__dev__propose_learning",
    "mcp__dev__merge_into_proposal",
    "mcp__dev__drop_candidates",
    "mcp__dev__stage_artifact",
    # Work-item phase pens. Each writes only inside its own bound item's folder.
    "mcp__dev__set_triage_classification",
    "mcp__dev__scaffold_artifact",
    "mcp__dev__record_verification",
    "mcp__dev__record_validation",
    "mcp__dev__check_plan_commands",
    "mcp__dev__record_diagnosis",
    "mcp__dev__record_lens",
    "mcp__dev__nominate_check",
    "mcp__dev__request_authorization",
    # Report pens. One factory, one contract: the path is code's, and an unfilled slot is refused.
    "mcp__dev__file_vet_report",
    "mcp__dev__file_plan_report",
    "mcp__dev__file_phase_report",
    "mcp__dev__write_checkpoint",
    # Merges trunk INTO the item's own worktree only; conflicts abort and report.
    "mcp__dev__sync_from_anchor_branch",
    # Writes the anchor docs through validated ops, at close, after the merge locked the code.
    "mcp__dev__apply_knowledge_edits",
    # Writes only the item's own plan.md, through validated ops — the re-plan's only way to change
    # it.
    "mcp__dev__revise_plan",
}


def is_safe(tool_name: str, input_data: dict) -> bool:
    """True if the tool may run without human approval."""
    return tool_name in SAFE_TOOLS
