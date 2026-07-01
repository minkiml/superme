"""Shared schema vocabulary — the locked enum value-sets (R5).

Each `Literal` here is pinned to exactly the values its producer can emit, verified against the live
data AND the producer code (so a `response_model` can declare it without ever 500-ing on a real row):

- work-item phase/status: written by `DevKnowledgeService` (create → queued/plan_design; advance;
  orchestrator `set_work_item_status`).
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

# --- work-item lifecycle (D-018) ---
WorkPhase = Literal["plan_design", "build_eval", "done"]
WorkStatus = Literal["queued", "in_progress", "waiting", "dropped"]

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
ArtifactKind = Literal["tool", "subagent", "skill", "mcp"]
