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
    # The dev agent's read-only dev event-log reader (PRD §4.9).
    "mcp__dev__read_dev_log",
    # The dev agent's read-only inbox reader (context-model-spec §5) — scoped to its own queue.
    "mcp__dev__read_inbox",
    # Read-only learning-pool readers (2026-07-11): candidate pool + standing OPEN proposals. Moved
    # into the general dev set so any session can answer "what learning is pending?" — mutate nothing.
    "mcp__dev__read_candidates",
    "mcp__dev__read_proposals",
    # Read-only run inspector (2026-07-12): one run's trace (calls + outcome) or the recent-run list,
    # scoped to this repo — the "what did activity #N do / why did it fail?" read + diagnose substrate.
    "mcp__dev__read_run",
    # The SANCTIONED itemize writes (work-item-session-recognition-prd): create one inbox item from a
    # discussion, or APPEND new discussion onto an existing item (the dedup path). Auto-allowed so a
    # general session can ticket work without a prompt; the one exemption to the general-session
    # guardrail (they can touch nothing but the inbox — create + append-only, never edit).
    "mcp__dev__create_inbox_item",
    "mcp__dev__append_inbox_item",
    # Capture SWEEP (WI-8) — the `capture` sub-agent's pen; files a candidate row from a swept
    # conversation slice. Nothing is applied here (the owner gate is downstream).
    "mcp__dev__file_candidate",
    # Memory PROCESSING (PRD §4.10.2) — file consolidated proposals from the candidate pool.
    # Still pre-gate: a proposal is a reversible draft awaiting the owner's accept/reject; the
    # apply step (which writes memory/ files) is what's actually gated, downstream.
    "mcp__dev__propose_memory",
    # Cross-run consolidation (2026-07-11): fold a recurring learning into the OPEN proposal that
    # already covers it, instead of minting a parallel proposal. Pre-gate and DB-only — mutates
    # un-ratified proposal rows (reverts a re-enriched draft to proposed for re-forge); nothing applied.
    "mcp__dev__merge_into_proposal",
    # Distill's gate (2026-07-10): permanently drop candidates that fail the four-test filter, so
    # noise never accretes in the pool. Pre-gate and self-contained — deletes only un-ratified
    # candidate rows (never run/transcript telemetry); no disk write, nothing applied.
    "mcp__dev__drop_candidates",
    # WRITE phase (WI-8 §Phase 3) — the `write` sub-agent's pen; stages the authored artifact into
    # the proposal row (→ drafted). Still pre-gate: staging writes only to the DB, never to disk;
    # the disk write (publish) is the owner-gated step downstream.
    "mcp__dev__stage_artifact",
    # Work-item phase-session pens (workspace-workflow S2/S5/S6): each enforces its own
    # bound_item_id scope (a session may touch only ITS item) and writes only inside that item's
    # own folder daemon-side — the sanctioned autonomous writes the D5 scaffold-then-fill playbook
    # depends on. Must never prompt: a headless phase run has no human to approve, and an
    # interactive one shouldn't page the owner for the item's own artifacts.
    # Triage's recording surface: kind + existing-deliverable onto the item's own yaml, triage
    # phase only (the gate that follows is the human confirmation).
    "mcp__dev__set_triage_classification",
    "mcp__dev__scaffold_artifact",
    "mcp__dev__record_validation_evidence",
    "mcp__dev__write_checkpoint",
    # Agent-run freshness sync (D9): merges trunk INTO the item's own worktree only — trunk itself
    # is never written; conflicts abort-and-report by default.
    "mcp__dev__sync_from_main",
    # Stages edit ops item-local (applied only later, atomically with the owner's merge).
    "mcp__dev__stage_knowledge_delta",
    # Proposal-only: drafts the verified closeout + pages the owner; never sets an item terminal.
    "mcp__dev__propose_close",
}


def is_safe(tool_name: str, input_data: dict) -> bool:
    """True if the tool may run without human approval."""
    return tool_name in SAFE_TOOLS
