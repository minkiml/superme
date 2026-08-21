"""Schemas for the system spine routes (system.py).

The Monitor/System dashboard read surface: the System singleton, the repo roster, and the run log,
plus the model-config + learning-switch write results.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import RunMode, RunStatus


class RunRow(BaseModel):
    """One row of the spine `run` table, live or historical — shared by every run-returning route.
    `feature` stays a free label."""
    id: int
    repo_id: str
    mode: RunMode
    feature: str
    # The phase this run happened in, NULL for non-item runs. An interactive turn's feature is
    # `chat`.
    phase: str | None = None
    session_id: str | None = None
    item_id: str | None = None
    status: RunStatus
    model: str | None = None
    tokens: int
    ctx_pct: int | None = None
    started_at: str
    ended_at: str | None = None
    # NULL while the origin session is live; set once it is hard-deleted, to label the preserved
    # orphan run.
    session_fate: str | None = None
    # A background run's structured completion outcome. NULL for interactive turns.
    outcome: str | None = None


class SystemResponse(BaseModel):
    """The System singleton: static config + the live half (in-flight runs) + the repo roster."""
    identity: str
    version: int
    default_model: str | None = None   # what a repo with no override runs (YAML → built-in floor)
    default_effort: str                # same, for reasoning effort
    policy_version: int
    default_repo: str
    learning_enabled: bool
    deputy_enabled: bool = True  # autopilot gate judge on/off
    deputy_strictness: dict[str, str] = Field(default_factory=dict)  # {gate: low·medium·high·extra}
    deputy_model: str | None = None            # the deputy's own tier (None = unset → the floor)
    deputy_effort: str | None = None
    deputy_effective_model: str | None = None  # what an unset picker actually resolves to
    deputy_effective_effort: str | None = None
    sweep_idle_seconds: int      # a dev session idle this long, with enough new content, gets swept
    sweep_poll_seconds: int      # how often the idle heartbeat scans (latency knob)
    sweep_min_user_msgs: int     # min new user turns past the watermark before a sweep fires (0 = off)
    live_runs: list[RunRow]
    running: int
    repos: list[str]


class RepoScope(BaseModel):
    """One (repo × scope) cell: home pointers + computed live status + counts."""
    knowledge_home: str
    operational_home: str
    active: bool
    current_item: str | None = None
    last_activity: str | None = None
    # Conversations: the sessions the owner can open and take a turn in. Headless agent threads
    # are excluded.
    sessions: int
    agents: int
    running: int


class RepoOverview(BaseModel):
    id: str
    label: str
    cwd: str
    layer: str
    model_override: str | None = None
    effort_override: str | None = None  # owner-set reasoning-effort override (None = inherit system)
    # The `vet` role's tier; None means the floor. Vet does not inherit the tier of what it
    # reviews.
    vet_model: str | None = None
    vet_effort: str | None = None
    learning_enabled: bool = True
    autopilot_concurrency: int = 4  # per-repo autopilot build⟷vet cap
    tag_color: str | None = None   # owner-set visual tag color (None = hashed-palette default)
    icon: str | None = None        # owner-set icon (emoji) shown in place of the color swatch
    review_mode: str = "fast"      # "fast" | "strict" — whether the diff gets its own review gate
    anchor_branch: str | None = None  # the branch every git site targets (None = the repo's default)
    resolved_anchor: str | None = None  # what `anchor_branch` resolves to right now (None = the repo
    # isn't a git repo, or the configured branch is missing — `anchor_error` says which)
    anchor_error: str | None = None  # set when the configured branch doesn't exist: every git site
    # refuses rather than falling back, so the settings UI must show it before a merge does
    scopes: dict[str, RepoScope]


class HoldQuestion(BaseModel):
    """One question the grill carries to the owner — the four fields `report_completion` enforces, so the
    card renders labelled rows instead of parsing prose."""
    question: str
    recommend: str | None = None
    why: str | None = None
    instead: str | None = None


class AttentionHold(BaseModel):
    """One parked work-item on the top-of-SuperMe attention center."""
    id: str
    title: str
    session_id: str | None = None  # the item's own dev session — so Open binds the chat to it, not the general thread
    phase: str | None = None
    cohort: str | None = None
    kind: Literal["question", "escalation", "paged", "review", "gate"]
    reason: str
    actor: str
    # The plan agent's clarifying questions, rendered as the ask-card. Absent on every other hold
    # kind.
    questions: list[HoldQuestion] | None = None


class RepoAttention(BaseModel):
    """A repo's holds, grouped so the center can label 'in <repo>'. Only repos WITH holds appear."""
    repo_id: str
    repo_label: str
    holds: list[AttentionHold]


class RepoConnectResponse(BaseModel):
    """A freshly connected repo. `onboarding` is the connect-time choice that its dev workspace launches
    until memory is established."""
    id: str
    label: str
    cwd: str
    onboarding: str | None = None


class RepoDisconnectResponse(BaseModel):
    """The receipt for a disconnect — what the cascade removed. Irreversible; the project folder is never
    touched, and run traces are preserved."""
    id: str
    label: str
    sessions_deleted: int      # session rows hard-deleted (transcripts removed with them)
    pipeline_rows_deleted: int  # inbox + learning candidate/proposal rows dropped
    knowledge_removed: bool    # superme-knowledge/<id>-knowledge/ deleted
    harness_removed: bool      # local-harness/<id>/ deleted (constitutions, assets, published)
    worktrees_removed: bool    # ~/.superme/worktrees/<id>/ deleted


class RunsResponse(BaseModel):
    live: list[RunRow]
    history: list[RunRow]
    running: int


class RunEventRow(BaseModel):
    """One entry of a run's event trail: a prompt, a reply block, a call, or that call's result.

    `tool_id` pairs a result back to its call; `parent_tool_id` names the spawn a row happened inside."""
    id: int
    seq: int
    kind: str
    name: str
    description: str | None = None
    tool_id: str | None = None
    parent_tool_id: str | None = None
    created_at: str


class RunTraceResponse(BaseModel):
    run_id: int
    events: list[RunEventRow]


class AgentModelRow(BaseModel):
    """One tunable background sub-agent: the TIER it tracks, and the concrete model that tier currently
    resolves to."""
    feature: str
    label: str
    scope: str
    tier: str
    model: str
    effort: str


class AgentModelsResponse(BaseModel):
    agents: list[AgentModelRow]


class LearningResponse(BaseModel):
    ok: bool
    learning_enabled: bool


class SweepConfigBody(BaseModel):
    """Partial update of the capture-sweep tuning — any omitted field is left unchanged."""
    idle_seconds: int | None = Field(default=None, ge=0)
    poll_seconds: int | None = Field(default=None, ge=0)
    min_user_msgs: int | None = Field(default=None, ge=0)


class SweepConfigResponse(BaseModel):
    ok: bool
    idle_seconds: int
    poll_seconds: int
    min_user_msgs: int


class CompactionConfigBody(BaseModel):
    """Partial update of the compaction runtime; omitted fields stay unchanged. The route refuses any
    trigger at or below the incompressible floor."""
    trigger_pct: int | None = Field(default=None, ge=1, le=100)
    by_kind: dict[str, int] | None = None   # per-kind trigger overrides {kind: pct}
    # "auto" (default) = reclaimable-normalized verdict; an int % = the manual escape hatch.
    min_gain_pct: int | Literal["auto"] | None = Field(default=None)


class CompactionConfigResponse(BaseModel):
    ok: bool
    trigger_pct: int          # fill % at which a work-item session compacts
    by_kind: dict[str, int]   # per-kind overrides
    # "auto" = judged against the session's reclaimable space; int % = flat manual threshold.
    min_gain_pct: int | Literal["auto"]
    floor_pct: int            # the static incompressible floor a trigger may never sit at/below
    # The lowest trigger actually accepted — floor plus working room. The FE's input `min` must
    # read THIS.
    min_pct: int


class RepoModelResponse(BaseModel):
    ok: bool
    repo_id: str
    role: str = "default"
    model: str | None = None
    effective: str | None = None


class RepoEffortResponse(BaseModel):
    ok: bool
    repo_id: str
    role: str = "default"
    effort: str | None = None
    effective: str


class RepoLearningResponse(BaseModel):
    ok: bool
    repo_id: str
    learning_enabled: bool


class RepoAutopilotResponse(BaseModel):
    ok: bool
    repo_id: str
    autopilot_concurrency: int


class DeputyConfigResponse(BaseModel):
    """The global deputy dial: whether a deputy judges autopilot gates + how readily it escalates
    per gate (triage/plan/review, each low·medium·high·extra)."""
    ok: bool
    deputy_enabled: bool
    deputy_strictness: dict[str, str]  # {gate: low·medium·high·extra}
    # `deputy_model` is what the owner set; the `effective_` pair is what a turn would actually
    # run on.
    deputy_model: str | None = None
    deputy_effort: str | None = None
    deputy_effective_model: str | None = None
    deputy_effective_effort: str | None = None


class RepoGitResponse(BaseModel):
    """The repo's two git knobs after a write.

    `resolved_anchor` is what `anchor_branch` points at now; `error` when the configured branch does
    not exist, which every git site refuses rather than falling back."""
    ok: bool
    repo_id: str
    review_mode: str
    anchor_branch: str | None = None
    resolved_anchor: str | None = None
    anchor_error: str | None = None


class RepoBranchesResponse(BaseModel):
    """The anchor picker's option set: this repo's local branches, newest-committed first, work-item
    branches excluded.

    `anchor` is what the anchor resolves to now, so the picker can show the branch in USE."""
    repo_id: str
    branches: list[str] = []
    anchor: str | None = None
    anchor_error: str | None = None


class RepoMetaResponse(BaseModel):
    ok: bool
    repo_id: str
    tag_color: str | None = None
    icon: str | None = None


# --- token observability ---
class TokenTypeSplit(BaseModel):
    """The systematic, per-token-type split. A run that never returned a final usage contributes nothing
    here — measured usage only. The four sum to the bucket total."""
    input: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output: int = 0


class CategoryNode(BaseModel):
    """One node of the semantic tree: a category total, its per-feature amounts, and how it should READ.

    `label` and `collapsed` are taxonomy decisions carried here, so no renderer has to re-make them."""
    total: int = 0
    features: dict[str, int] = {}
    label: str = ""
    collapsed: bool = False


class TokenBucket(BaseModel):
    """A token total plus its splits: `by_category` is semantic, `by_type` systematic, and the two
    reconcile. All maps are open, not a locked enum."""
    total: int
    by_scope: dict[str, int] = {}
    by_feature: dict[str, int] = {}
    # Per-feature cache_read, global only, so a drilldown can render 4-type. Empty on per-repo
    # buckets.
    by_feature_cache_read: dict[str, int] = {}
    by_type: TokenTypeSplit = TokenTypeSplit()
    by_category: dict[str, CategoryNode] = {}


class RepoTokens(TokenBucket):
    runs: int = 0


class ArchivedRepoTokens(BaseModel):
    """One disconnected project's preserved spend. `label` is the tombstoned display name, falling back
    to the bare id."""
    id: str
    label: str
    total: int = 0
    runs: int = 0
    disconnected_at: str | None = None


class ArchivedTokens(BaseModel):
    """Spend belonging to repos that are no longer connected.

    Their runs are kept forever, so they still count in the global total; this bucket keeps them
    attributable."""
    total: int = 0
    runs: int = 0
    repos: list[ArchivedRepoTokens] = []


class TokenUsageResponse(BaseModel):
    """System-wide token usage: the global bucket, one bucket per repo, and the roll-up of disconnected
    repos."""
    global_: TokenBucket = Field(alias="global")
    by_repo: dict[str, RepoTokens] = {}
    archived: ArchivedTokens = ArchivedTokens()

    model_config = ConfigDict(populate_by_name=True)


class TokenDay(BaseModel):
    """One local-day bucket of the usage time-series: the four token types, the day total, and the
    running cumulative."""
    day: str
    input: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output: int = 0
    total: int = 0
    cumulative: int = 0
    runs: int = 0


class TokenTimeseriesResponse(BaseModel):
    """Per-day token usage for the trend graph, bucketed by the caller's local day."""
    days: list[TokenDay] = []
    total: int = 0


class RepoModelBody(BaseModel):
    model: str | None = None  # null/"" clears this repo's override (fall back to the default)
    # Which role's tier this sets; omitted means the project's own. `vet` resolves on its own
    # chain.
    role: str = "default"


class RepoEffortBody(BaseModel):
    effort: str | None = None  # null/"" clears this repo's override (fall back to the default)
    role: str = "default"


class LearningBody(BaseModel):
    enabled: bool


class DeputyConfigBody(BaseModel):
    # Partial update: `strictness` is a per-gate map, so send only the gates that changed.
    enabled: bool | None = None
    strictness: dict[str, str] | None = None  # {triage|plan|review: low·medium·high·extra}
    # One judge across every project, so one answer, set here rather than per repo. Never the
    # project's tier.
    model: str | None = None
    effort: str | None = None


class AgentModelBody(BaseModel):
    # Either/both may be sent. model = a TIER (`sonnet`) or concrete id; effort = low|medium|high.
    model: str | None = None
    effort: str | None = None


class RepoMetaBody(BaseModel):
    # None = leave the field unchanged; "" = clear it (back to defaults).
    color: str | None = None
    icon: str | None = None


class RepoConnectBody(BaseModel):
    path: str                    # absolute dir to link (created if kind=new)
    label: str | None = None     # display name (defaults to the dir name)
    kind: str                    # "new" (greenfield → project-init) | "existing" (code → retrofit)


class AutopilotConcurrencyBody(BaseModel):
    concurrency: int


class RepoGitBody(BaseModel):
    # None = leave unchanged; "" clears anchor_branch back to "derive the repo's default branch".
    review_mode: str | None = None
    anchor_branch: str | None = None
