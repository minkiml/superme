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
    # Base tools (every mode): read-only on-demand loader for a constitution body by name, and the
    # read-only asset-pool ranker (suggests relevant pooled knowledge). Must never prompt — the
    # frontmatter-first model depends on cheap pulls, and onboarding calls suggest_assets each run.
    "mcp__superme__pull_constitution",
    "mcp__superme__suggest_assets",
    # The agent's read-only Slack readers (in-process MCP tools).
    "mcp__slack__read_channel", "mcp__slack__read_thread",
    # The dev agent's read-only activity-log reader (PRD §4.9).
    "mcp__dev__dev_log",
    # The dev agent's read-only inbox reader (context-model-spec §5) — scoped to its own queue.
    "mcp__dev__list_inbox",
    # The SANCTIONED itemize writes (work-item-session-recognition-prd): create one inbox item from a
    # discussion, or APPEND new discussion onto an existing item (the dedup path). Auto-allowed so a
    # general session can ticket work without a prompt; the one exemption to the general-session
    # guardrail (they can touch nothing but the inbox — create + append-only, never edit).
    "mcp__dev__create_inbox_item",
    "mcp__dev__append_inbox_item",
    # Capture SWEEP (WI-8) — the `capture` sub-agent's pen; files a candidate row from a swept
    # conversation slice. Nothing is applied here (the owner gate is downstream).
    "mcp__dev__file_candidate",
    # Memory PROCESSING (PRD §4.10.2) — read the candidate pool, file consolidated proposals.
    # Still pre-gate: a proposal is a reversible draft awaiting the owner's accept/reject; the
    # apply step (which writes memory/ files) is what's actually gated, downstream.
    "mcp__dev__review_candidates",
    "mcp__dev__propose_memory",
    # Cross-run consolidation (2026-07-11): read the standing OPEN proposals and fold a recurring
    # learning into the one that already covers it, instead of minting a parallel proposal. Pre-gate
    # and DB-only — mutates un-ratified proposal rows (reverts a re-enriched draft to proposed for
    # re-forge); no disk write, nothing applied.
    "mcp__dev__review_proposals",
    "mcp__dev__merge_into_proposal",
    # Distill's gate (2026-07-10): permanently drop candidates that fail the four-test filter, so
    # noise never accretes in the pool. Pre-gate and self-contained — deletes only un-ratified
    # candidate rows (never run/transcript telemetry); no disk write, nothing applied.
    "mcp__dev__drop_candidates",
    # WRITE phase (WI-8 §Phase 3) — the `write` sub-agent's pen; stages the authored artifact into
    # the proposal row (→ drafted). Still pre-gate: staging writes only to the DB, never to disk;
    # the disk write (publish) is the owner-gated step downstream.
    "mcp__dev__stage_artifact",
}


def is_safe(tool_name: str, input_data: dict) -> bool:
    """True if the tool may run without human approval."""
    return tool_name in SAFE_TOOLS
