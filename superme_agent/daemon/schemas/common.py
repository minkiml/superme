"""Shared schema vocabulary — the locked enum value-sets.

Each `Literal` is pinned to exactly the values its producer can emit, so a `response_model` never
500s on a real row. Model-generated and free-form fields are deliberately left unlocked.
"""

from typing import Literal

# --- work-item lifecycle --- Three state layers: `kind` picks the machinery, `phase` the pipeline
# stage, `status` the runnable state.
WorkKind = Literal["implementation", "research"]
WorkPhase = Literal["triage", "plan", "build", "vet", "review", "investigate", "close"]
# `error` is an item whose work STOPPED, so it stays where it died carrying `error_reason`. Never
# terminal.
WorkStatus = Literal["active", "awaiting_child", "awaiting_upstream", "awaiting_slot",
                     "awaiting_human", "error", "done"]
WorkOutcome = Literal["completed", "abandoned", "superseded"]
# Blocking and parallel are children that gate the parent's completion; spawn is provenance only.
SpawnRelation = Literal["blocking", "parallel", "spawn"]
# Per-run terminal outcomes, stamped by the background phase completion contract.
RunOutcome = Literal["success", "clean_noop", "blocked", "approval_required", "exhausted", "stagnated"]

# --- inbox triage ---
# `item` becomes a work-item on push; `note` is the owner's own, never pushed.
InboxKind = Literal["item", "note"]
InboxStatus = Literal["open", "pushed"]
InboxOrigin = Literal["user", "agent"]

# --- memory proposals (two-gate learning loop) ---
OutputForm = Literal["constitution", "skill", "agent"]
TargetScope = Literal["repo_dev", "universal_dev", "core"]
ProposalStatus = Literal[
    "proposed", "writing", "drafted", "published", "rejected", "dropped", "superseded", "retired"
]

# --- spine runs ---
RunMode = Literal["core", "dev"]
RunStatus = Literal["running", "done", "aborted", "waiting"]

# --- activity log + call-trail ---
EventScope = Literal["item", "dev", "global"]
# Distinct actors, so an autopilot advance and a deputy approval never read as the same hand.
EventActor = Literal["owner", "agent", "daemon", "autopilot", "deputy"]
# All seven kinds ride one feed, because the Runs pane groups by RUN and a text-only run must
# still appear.
ArtifactKind = Literal["tool", "subagent", "skill", "mcp", "result", "prompt", "reply"]
