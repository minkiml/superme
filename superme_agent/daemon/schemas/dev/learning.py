"""Response schemas for the learning routes: stats, ops, and the two-gate review queue.

The rich DB rows use `extra='allow'` and emit only the keys they return; the stats payload is
modeled strictly.
"""

from pydantic import BaseModel, ConfigDict

from ..common import OutputForm, TargetScope, ProposalStatus


# --- /dev/memory/stats (fixed shape) ---

class CandidateStatItem(BaseModel):
    id: int
    signal: str
    form_hint: str | None = None
    scope_hint: str | None = None
    source: str | None = None
    captured_at: str | None = None


class CandidatesStat(BaseModel):
    total: int
    pending_proposals: int
    drafted_proposals: int
    by_form: dict[str, int]
    items: list[CandidateStatItem]


class KnowledgeStatItem(BaseModel):
    name: str
    description: str
    type: str
    enabled: bool
    source: str


class KnowledgeStat(BaseModel):
    facts_total: int
    facts_enabled: int
    facts_disabled: int
    facts_by_type: dict[str, int]
    artifacts_total: int
    artifacts_reserved: bool
    items: list[KnowledgeStatItem]


class MemoryStatsResponse(BaseModel):
    context_id: str
    distilling: bool
    candidates: CandidatesStat
    knowledge: KnowledgeStat


class ScopeCount(BaseModel):
    """A count split by knowledge scope: dev (repo_dev + universal_dev) vs core."""
    dev: int = 0
    core: int = 0
    total: int = 0


class LearningRollupRepo(BaseModel):
    repo_id: str
    label: str
    candidates: ScopeCount
    pending: ScopeCount
    drafted: ScopeCount
    learned: ScopeCount


class LearningRollupResponse(BaseModel):
    """Per-repo learning counts across the 4-slot pipeline (candidates · pending · drafted · learned),
    each split by dev/core scope, plus the cross-repo totals. Powers the Learning tile drill-in."""
    repos: list[LearningRollupRepo]
    candidates: ScopeCount
    pending: ScopeCount
    drafted: ScopeCount
    learned: ScopeCount


# --- distill / sweep ops (branch-variant; exclude_unset on the routes) ---

class DistillResponse(BaseModel):
    status: str
    context_id: str
    candidates: int | None = None


class SweepResponse(BaseModel):
    status: str
    session_id: str
    filed: int | None = None
    watermark: int | None = None


class SweptItem(BaseModel):
    session_id: str
    repo_id: str
    filed: int


class IdleScanResponse(BaseModel):
    scanned: int
    swept: list[SweptItem]


# --- proposal review queue ---

class ProposalCandidate(BaseModel):
    """A source candidate behind a proposal (provenance shown in the review popup)."""
    model_config = ConfigDict(extra="allow")
    id: int
    signal: str | None = None
    rationale: str | None = None
    evidence: str | None = None
    form_hint: str | None = None
    scope_hint: str | None = None
    status: str | None = None
    source: str | None = None
    captured_at: str | None = None


class EvalMetrics(BaseModel):
    """The artifact's own run cost on a synthetic task (forge_kit/eval.py). kind 'run' = skill/agent
    measured once; kind 'overhead' = constitution's always-on per-turn cost. Tolerant of pre-metrics
    and error rows (everything optional, extra='allow' for forward-compat)."""
    model_config = ConfigDict(extra="allow")
    kind: str | None = None
    context_tokens: int | None = None
    output_tokens: int | None = None
    turns: int | None = None
    duration_s: float | None = None
    cost_usd: float | None = None
    tokens_per_turn: float | None = None
    capped: str | None = None
    error: str | None = None
    tokens: int | None = None  # legacy (pre-footprint proposals)


class EvalReport(BaseModel):
    """A proposal's gate-2 eval report, stored on the row.

    Versioned via `schema_version`, and tolerant of legacy reports: every field is optional, so an older
    or richer one never 500s a read."""
    model_config = ConfigDict(extra="allow")
    schema_version: int | None = None
    form: str | None = None
    verdict: str | None = None  # pass | warn | fail | skipped | unknown
    summary: str | list[str] | None = None
    checks: list[dict] | None = None
    issues: list[dict] | None = None
    metrics: EvalMetrics | None = None
    trial_task: str | None = None


class Proposal(BaseModel):
    """A memory proposal — a rich DB row with JSON columns.

    The declared fields document the stable shape; `extra='allow'` carries the rest. The enums are
    locked, because their producer coerces to exactly those sets."""
    model_config = ConfigDict(extra="allow")
    id: int
    context_id: str | None = None
    created_at: str | None = None
    output_form: OutputForm | None = None
    target_scope: TargetScope | None = None
    apply_target: str | None = None
    cluster: str | None = None
    title: str | None = None
    body: str | None = None
    rationale: str | None = None
    # confidence is model-generated (distill) and uncoerced → stays a loose string, NOT a Literal.
    confidence: str | None = None  # categorical: "high" | "medium" | "low" (NOT numeric)
    candidate_ids: list[int] | None = None
    status: ProposalStatus | None = None
    recall_type: str | None = None  # always null on new rows
    summary: str | None = None
    fields: dict | None = None
    clarifications: list | None = None
    clarification_answers: dict | list | None = None
    staged_artifact: str | None = None
    staged_path: str | None = None
    eval_report: EvalReport | None = None
    candidates: list[ProposalCandidate] | None = None


class ProposalsResponse(BaseModel):
    context_id: str
    proposals: list[Proposal]


class ExecutionStep(BaseModel):
    """One lifecycle event in a proposal's execution trace (real events + a synthetic 'filed' head)."""
    model_config = ConfigDict(extra="allow")
    kind: str
    actor: str | None = None
    summary: str | None = None
    created_at: str | None = None
    meta: dict | None = None


class ProposalExecutionResponse(BaseModel):
    context_id: str
    proposal_id: int
    status: str
    steps: list[ExecutionStep]


class ApproveResponse(BaseModel):
    status: str
    proposal_id: int


class ProposalActionResponse(BaseModel):
    """reject / drop / artifact-edit: ok + the updated proposal."""
    ok: bool
    proposal: Proposal


class PublishResponse(BaseModel):
    ok: bool
    path: str
    proposal: Proposal
