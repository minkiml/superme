"""Response schemas for the system spine routes (system.py).

The Monitor/System dashboard read surface: the System singleton, the repo roster, and the run log,
plus the model-config + learning-switch write results.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .common import RunMode, RunStatus


class RunRow(BaseModel):
    """One row of the spine `run` table (live or historical) — every run-returning route shares it.
    mode/status are locked (R5: the spine writes exactly these); `feature` stays a free label."""
    id: int
    repo_id: str
    mode: RunMode
    feature: str
    # The work-item phase this run happened IN (triage/plan/build/vet/review/close), stamped at run
    # open. NULL for non-item runs. Surfaced so the Activity feed shows the pipeline phase, not just
    # the raw feature label (an interactive triage/build turn is feature `chat` — phase is the signal).
    phase: str | None = None
    session_id: str | None = None
    item_id: str | None = None
    status: RunStatus
    model: str | None = None
    tokens: int
    ctx_pct: int | None = None
    started_at: str
    ended_at: str | None = None
    # NULL while the origin session is live; 'deleted' / 'retired' once it's hard-deleted
    # (session-deletion-trace-model) — the run + its trace are preserved, this labels the orphan.
    session_fate: str | None = None
    # A BACKGROUND run's structured completion outcome (workspace-workflow D2/S5): success |
    # partial | clean_noop | blocked | approval_required | exhausted | stagnated. NULL for
    # interactive turns and pre-S5 rows. Feeds the S7 attention engine.
    outcome: str | None = None


class SystemResponse(BaseModel):
    """The System singleton: static config + the live half (in-flight runs) + the repo roster."""
    identity: str
    version: int
    default_model: str | None = None
    default_model_static: str | None = None
    default_model_overridden: bool
    default_effort: str            # effective system reasoning effort (runtime → YAML → "medium")
    default_effort_overridden: bool
    policy_version: int
    default_repo: str
    learning_enabled: bool
    deputy_enabled: bool = True            # autopilot gate judge on/off (slice 4)
    deputy_strictness: dict[str, str] = Field(default_factory=dict)  # {gate: low·medium·high·extra}
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
    # CONVERSATIONS — the sessions the owner can open and take a turn in. The headless build/vet
    # agent threads are excluded (kind_profiles.AGENT_THREAD_KINDS) so this agrees with the session
    # picker; surfaced as "conversations", not "sessions".
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
    learning_enabled: bool = True
    autopilot_concurrency: int = 4  # per-repo autopilot build⟷vet cap (slice 3)
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
    """One question the grill carries to the owner — the four fields `report_completion` enforces,
    so the card renders labelled rows instead of parsing prose. `recommend`/`why` are absent only
    on a pre-typed report replayed from an older event."""
    question: str
    recommend: str | None = None
    why: str | None = None
    instead: str | None = None


class AttentionHold(BaseModel):
    """One parked (`awaiting_human`) work-item on the top-of-SuperMe attention center (Pass 2 · Q2)."""
    id: str
    title: str
    session_id: str | None = None  # the item's own dev session — so Open binds the chat to it, not the general thread
    phase: str | None = None
    cohort: str | None = None
    kind: Literal["question", "escalation", "breaker", "paged", "review", "gate"]
    reason: str
    actor: str
    # kind "question" only — the plan agent's clarifying questions (renovation §2 grill), rendered
    # as the ask-card. Absent on every other hold kind.
    questions: list[HoldQuestion] | None = None


class RepoAttention(BaseModel):
    """A repo's holds, grouped so the center can label 'in <repo>'. Only repos WITH holds appear."""
    repo_id: str
    repo_label: str
    holds: list[AttentionHold]


class RepoConnectResponse(BaseModel):
    """A freshly connected repo — the new orbit node. `onboarding` is the connect-time choice
    (project-init | retrofit) that its dev workspace launches until memory is established."""
    id: str
    label: str
    cwd: str
    onboarding: str | None = None


class RepoDisconnectResponse(BaseModel):
    """The receipt for a disconnect — what the cascade actually removed. Irreversible by design;
    the project folder itself is never touched, and run traces + dev events are preserved
    (never-delete-logs), stamped session_fate='disconnected'."""
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
    """One entry of a run's event trail: a prompt, an assistant reply block, a tool/skill/agent call,
    or that call's `result`. `kind` ∈ prompt | reply | tool | mcp | skill | agent | subagent | result;
    `name` is the label, `description` the body; `tool_id` pairs a result back to its call."""
    id: int
    seq: int
    kind: str
    name: str
    description: str | None = None
    tool_id: str | None = None
    created_at: str


class RunTraceResponse(BaseModel):
    run_id: int
    events: list[RunEventRow]


class SystemModelResponse(BaseModel):
    ok: bool
    model: str | None = None
    effective: str | None = None


class AgentModelRow(BaseModel):
    """One tunable background sub-agent: the TIER it tracks (sonnet/opus/haiku — the pick) and the
    concrete model that tier currently resolves to (what actually runs), plus its label and scope."""
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
    """Partial update of the compaction runtime (S8/D11) — omitted fields stay unchanged. The
    route refuses (409) any trigger at/below the incompressible floor."""
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


class RepoModelResponse(BaseModel):
    ok: bool
    repo_id: str
    model: str | None = None
    effective: str | None = None


class SystemEffortResponse(BaseModel):
    ok: bool
    effort: str | None = None
    effective: str


class RepoEffortResponse(BaseModel):
    ok: bool
    repo_id: str
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


class RepoGitResponse(BaseModel):
    """The repo's two git knobs after a write (workflow-renovation-v2 §2.2). `resolved_anchor` is
    what `anchor_branch` actually points at now — None when the repo isn't a git repo; `error` when
    the configured branch doesn't exist, which is a refusal at every git site, not a fallback."""
    ok: bool
    repo_id: str
    review_mode: str
    anchor_branch: str | None = None
    resolved_anchor: str | None = None
    anchor_error: str | None = None


class RepoMetaResponse(BaseModel):
    ok: bool
    repo_id: str
    tag_color: str | None = None
    icon: str | None = None


# --- token observability (token-usage-tracking-spec) ----------------------------
class TokenTypeSplit(BaseModel):
    """Breakdown 2 — the systematic (per token-type) split. `legacy` holds pre-migration rows that
    only carry the old collapsed scalar. The five sum to the bucket total (reconciliation)."""
    input: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output: int = 0
    legacy: int = 0


class CategoryNode(BaseModel):
    """One node of Breakdown 1 — the semantic tree: a category total + its per-feature amounts."""
    total: int = 0
    features: dict[str, int] = {}


class TokenBucket(BaseModel):
    """A token total plus its splits. Two reconciling breakdowns: `by_category` (semantic — the
    generic tree the FE renders) and `by_type` (systematic). `by_scope`/`by_feature` are retained
    flat maps for back-compat. All maps are open (producer-supplied labels, not a locked enum)."""
    total: int
    by_scope: dict[str, int] = {}
    by_feature: dict[str, int] = {}
    # Per-feature cache_read (global only) — lets the drilldown's "By operation" tab render 4-type
    # (per-feature 3-type + this) when the owner toggles cache_read on. Empty on per-repo buckets.
    by_feature_cache_read: dict[str, int] = {}
    by_type: TokenTypeSplit = TokenTypeSplit()
    by_category: dict[str, CategoryNode] = {}


class RepoTokens(TokenBucket):
    runs: int = 0


class ArchivedRepoTokens(BaseModel):
    """One disconnected project's preserved spend. `label` is the tombstoned display name (falls
    back to the bare id for repos that left before tombstoning); `disconnected_at` is null there."""
    id: str
    label: str
    total: int = 0
    runs: int = 0
    disconnected_at: str | None = None


class ArchivedTokens(BaseModel):
    """"Old projects" — spend belonging to repos that are no longer connected. Their runs are kept
    forever, so they still count in `global.total`; this bucket keeps them ATTRIBUTABLE (the orbit
    only renders live repos, so without it the visible nodes wouldn't sum to the header)."""
    total: int = 0
    runs: int = 0
    repos: list[ArchivedRepoTokens] = []


class TokenUsageResponse(BaseModel):
    """System-wide token usage: the global bucket + one bucket per repo (keyed by repo id) +
    the "Old projects" roll-up of disconnected repos (also present in by_repo, by raw id)."""
    global_: TokenBucket = Field(alias="global")
    by_repo: dict[str, RepoTokens] = {}
    archived: ArchivedTokens = ArchivedTokens()

    model_config = ConfigDict(populate_by_name=True)


class TokenDay(BaseModel):
    """One local-day bucket of the usage time-series: the four token types (+ legacy), the day
    total, and the running `cumulative` total across days."""
    day: str
    input: int = 0
    cache_creation: int = 0
    cache_read: int = 0
    output: int = 0
    legacy: int = 0
    total: int = 0
    cumulative: int = 0
    runs: int = 0


class TokenTimeseriesResponse(BaseModel):
    """Per-day token usage for the trend graph, bucketed by the caller's local day."""
    days: list[TokenDay] = []
    total: int = 0
