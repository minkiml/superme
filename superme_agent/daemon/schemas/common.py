"""Shared schema vocabulary — the locked enum value-sets (R5).

Each `Literal` here is pinned to exactly the values its producer can emit, verified against the live
data AND the producer code (so a `response_model` can declare it without ever 500-ing on a real row):

- work-item kind/phase/status/outcome: written by `DevKnowledgeService` (create → triage/active;
  advance via KIND_PROFILES; orchestrator `set_work_item_status`; terminal via outcome).
- inbox kind/status/origin: `DevStore` validates against `_KINDS`/`_STATUSES` on write.
- proposal output_form/target_scope: `DevStore.propose_memory` COERCES to `_MEM_OUTPUT_FORMS`/
  `_MEM_TARGET_SCOPES`; proposal status is guarded by `_MEM_PROP_STATUSES` on every transition.
- run mode/status: `SystemSpine` (mode = the core|dev scope axis; status flips running→done/waiting/
  aborted).
- event scope/actor and artifact-call kind: daemon-emitted, fully code-controlled.

Deliberately NOT locked (model-generated / free-form, so a Literal could 500): proposal `confidence`
(distill emits high|medium|low but it is uncoerced text), `recall_type` (retired, always null now),
`apply_target` (a slug), run `feature` (a free label: chat|plan|distill|sweep|write|…), event `kind`.
"""

from typing import Literal

# --- work-item lifecycle (workspace-workflow D1/D2, 2026-07-15) ---
# Three separate state layers replace the old conflated phase/status pair:
# 1. `kind` — which MACHINERY the item runs (core/kind_profiles.py; unknown kind fails loud).
# 2. `phase` — per-kind ordered pipeline stage. implementation: triage→plan→build→validate→
#    deliver→close · research: triage→plan→investigate→report→close. The Literal is the UNION of
#    all kinds' phases (KIND_PROFILES.ALL_PHASES); validity-per-kind is enforced by kind_profiles.
# 3. `status` — the runnable-state axis: active · awaiting_* (typed router — only awaiting_human
#    pages the owner; awaiting_child auto-resumes via core/status_router.py) · done (terminal).
# `outcome` stamps HOW an item ended (with status=done): completed | abandoned | superseded —
# status changes only, never deletes (never-delete standing constraint).
WorkKind = Literal["implementation", "research"]
WorkPhase = Literal["triage", "plan", "build", "validate", "deliver", "investigate", "report", "close"]
WorkStatus = Literal["active", "awaiting_child", "awaiting_human", "done"]
WorkOutcome = Literal["completed", "abandoned", "superseded"]
# Branch-off relation on `spawned_from` (D3): blocking/parallel = children (auto-pushed, gate the
# parent's completion; blocking additionally pauses it) · spawn = provenance only (owner-pushed).
SpawnRelation = Literal["blocking", "parallel", "spawn"]
# Per-run terminal outcomes (D2 layer 3) — stamped by the headless phase-session completion
# contract (lands S5); declared here so the wire vocabulary is stable from S1.
RunOutcome = Literal["success", "clean_noop", "blocked", "approval_required", "exhausted", "stagnated"]

# --- inbox triage ---
InboxKind = Literal["note", "idea", "todo", "question"]
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
EventActor = Literal["owner", "agent", "daemon"]
ArtifactKind = Literal["tool", "subagent", "skill", "mcp", "result"]
