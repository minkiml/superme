"""Response schemas for the dev work-item routes (routers/dev/work_items.py).

The `WorkItem` model covers BOTH the 14-field base shape (read_work_item / create_work_item, used by
the detail + inbox-push routes) and the 28-field enriched shape (the /dev list: base + tree-walk +
run telemetry). All non-core fields are optional and routes use `response_model_exclude_unset=True`,
so each route emits exactly the keys it actually returns (no spurious nulls, no dropped fields).
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from ..common import WorkPhase, WorkStatus, ArtifactKind


class ArtifactRef(BaseModel):
    """A work-item design artifact, NORMALIZED on read (R5): legacy bare-string entries
    ("artifacts/plan.md") and structured {type, path} entries both coerce to this one shape at the
    daemon boundary (`DevKnowledgeService`), so the wire contract is a single object union, never a
    string-or-object mix."""
    type: str
    path: str


class WorkItemLastRun(BaseModel):
    tokens: int
    duration_ms: int | None = None
    model: str | None = None
    context_pct: int | None = None


class WorkItemTasks(BaseModel):
    done: int
    total: int


class WorkItem(BaseModel):
    """A dev work-item. Base fields are always present; tree-walk + telemetry fields appear only on
    the enriched /dev list (extra='allow' tolerates any future frontmatter key)."""
    model_config = ConfigDict(extra="allow")

    # --- base (read_work_item / create_work_item) ---
    id: str
    root_id: str | None = None
    parent_id: str | None = None
    # Two-tier anchor-scaffold pointer, set on a ROOT work-item: `wave` = the roadmap wave this item
    # is an instance of (resolves its deliverable), or `deliverable` directly when no wave applies.
    # Self-describing / derived by scan, like root_id/parent_id. Written in S2; declared here as the
    # contract so the wire shape is stable.
    wave: str | None = None
    deliverable: str | None = None
    title: str | None = None
    phase: WorkPhase | None = None
    status: WorkStatus | None = None
    model: str | None = None
    effort: str | None = None  # configured reasoning effort (low|medium|high) its runs use
    # work-item frontmatter dates parse to datetime.date (YAML); a date|str union keeps them faithful
    # (date → isoformat "YYYY-MM-DD" on serialize, exactly as the raw jsonable_encoder path did).
    done_at: date | str | None = None
    artifacts: list[ArtifactRef] | None = None
    blocked_by: list[str] | None = None
    session_id: str | None = None
    created_at: date | str | None = None
    updated_at: date | str | None = None
    description: str | None = None
    # --- tree-walk (read_all) ---
    depth: int | None = None
    folder: str | None = None
    # child branch-off ids (computed in DevKnowledgeService.read_all), NOT nested items.
    children: list[str] | None = None
    blocked: bool | None = None
    # --- run telemetry (the /dev route) ---
    total_tokens: int | None = None
    # Per-phase token accumulation {phase → Σ}, both bases: `phase_tokens` = 3-type (what the card
    # shows for its current phase), `phase_tokens_4type` = full volume (3-type + cache_read) behind it.
    phase_tokens: dict[str, int] | None = None
    phase_tokens_4type: dict[str, int] | None = None
    last_run: WorkItemLastRun | None = None
    running: bool | None = None
    run_started_at: float | None = None
    run_tokens: int | None = None
    run_model: str | None = None
    run_context_pct: int | None = None
    context_pct: int | None = None
    tasks: WorkItemTasks | None = None


class TaskItem(BaseModel):
    """One tasks.md checklist line."""
    text: str
    done: bool


class ArtifactCall(BaseModel):
    """One row of a work-item's run call-trail (tool / sub-agent / skill invocation)."""
    id: int
    run_id: int | None = None
    seq: int
    kind: ArtifactKind
    name: str
    description: str | None = None
    created_at: str


# --- route responses ---

class PlanResponse(BaseModel):
    ok: bool
    status: str
    work_item_id: str
    model: str


class WorkItemDeleteResponse(BaseModel):
    ok: bool
    id: str
    session_cleared: bool
    inbox_removed: int | None = None


class WorkItemDetailResponse(BaseModel):
    item: WorkItem
    plan: str | None = None
    prd: str | None = None
    tasks: list[TaskItem] | None = None
    execution: str | None = None


class WorkItemArtifactsResponse(BaseModel):
    artifacts: list[ArtifactCall]


class WorkItemCompleteResponse(BaseModel):
    ok: bool
    id: str
    archived: str
    session_cleared: bool
    runs_freed: int


class WorkItemModelResponse(BaseModel):
    ok: bool
    id: str
    model: str


class WorkItemEffortResponse(BaseModel):
    ok: bool
    id: str
    effort: str


class WorkItemAdvanceResponse(BaseModel):
    ok: bool
    id: str
    phase: str
    # `from` is a Python keyword → alias it; FastAPI serializes by alias, emitting "from".
    from_: str = Field(alias="from")


class WorkItemScaffoldResponse(BaseModel):
    """Result of setting a root work-item's anchor pointer (wave OR deliverable)."""
    ok: bool
    id: str
    wave: str | None = None
    deliverable: str | None = None
