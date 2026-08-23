"""Schemas for the dev work-item routes.

`WorkItem` covers both the base shape and the enriched list shape. All non-core fields are optional
and routes use `response_model_exclude_unset=True`, so each emits exactly the keys it returns.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from ..common import WorkKind, WorkPhase, WorkStatus, WorkOutcome, SpawnRelation, ArtifactKind


class SpawnedFrom(BaseModel):
    """The provenance edge, child-side: which item this one branched off and how.

    `blocking` and `parallel` gate the parent's completion; `spawn` is provenance only."""
    item: str
    relation: SpawnRelation
    note: str | None = None


class ArtifactRef(BaseModel):
    """A work-item design artifact, NORMALIZED on read: legacy bare-string and structured entries both
    coerce to this shape at the daemon boundary, so the wire contract is never a string-or-object
    mix."""
    type: str
    path: str


class WorkItemLastRun(BaseModel):
    tokens: int
    duration_ms: int | None = None
    model: str | None = None
    ctx_pct: int | None = None
    # Epoch seconds at which the newest finished run ENDED — the card's "3 minutes ago".
    ended_at: float | None = None


class WorkItemTasks(BaseModel):
    done: int
    total: int


class WorkItem(BaseModel):
    """A dev work-item. Base fields are always present; tree-walk and telemetry fields appear only on the
    enriched list."""
    model_config = ConfigDict(extra="allow")

    # --- base (read_work_item / create_work_item) ---
    id: str
    root_id: str | None = None
    parent_id: str | None = None
    # Anchor pointer on a ROOT item: the roadmap wave it instantiates, or a deliverable directly
    # when no wave applies.
    wave: str | None = None
    deliverable: str | None = None
    title: str | None = None
    # `kind` picks the machinery, `spawned_from` is the branch-off edge, `outcome` stamps how the
    # item ended.
    kind: WorkKind | None = None
    # Which research family, on a `research` item. Declared here because the surface labels it.
    research_kind: str | None = None
    # What the FILER said this item was, frozen at birth. Shown only where it differs from `kind`.
    proposed_kind: WorkKind | None = None
    spawned_from: SpawnedFrom | None = None
    superseded_by: str | None = None
    outcome: WorkOutcome | None = None
    inbox_id: int | None = None
    phase: WorkPhase | None = None
    status: WorkStatus | None = None
    # Present ONLY while `status == "error"`, cleared as the item leaves it, so it cannot describe
    # a resolved stop.
    error_reason: str | None = None
    # Ids this item may not start before; an item with an open upstream rests until the scheduler
    # releases it.
    after: list[str] | None = None
    # Autopilot: the per-item policy — does the workflow drive its gates without a click.
    autopilot: bool | None = None
    model: str | None = None
    effort: str | None = None  # configured reasoning effort (low|medium|high) its runs use
    # Frontmatter dates parse to `date`, so the union keeps them faithful through serialization.
    done_at: date | str | None = None
    # A terminal item KEEPS THE WHOLE record: clearance removes the directory but the field
    # records where the work happened.
    git_branch: str | None = None
    git_worktree: str | None = None
    git_base: str | None = None
    git_merge_commit: str | None = None
    git_merged_at: str | None = None
    git_backup_ref: str | None = None
    # Set with no merge commit means the PR is open; the FE derives it the same way the backend
    # does.
    git_pr_opened_at: str | None = None
    # The owner-opened read receipt: a terminal item without it sits in the `unread` bucket. Never
    # bumps `updated_at`.
    seen_at: datetime | str | None = None
    artifacts: list[ArtifactRef] | None = None
    session_id: str | None = None
    created_at: date | str | None = None
    updated_at: date | str | None = None
    description: str | None = None
    # --- tree-walk (read_all) ---
    depth: int | None = None
    folder: str | None = None
    # child branch-off ids (computed in DevKnowledgeService.read_all), NOT nested items.
    children: list[str] | None = None
    # --- run telemetry (the /dev route) ---
    total_tokens: int | None = None
    # Per-phase accumulation in both bases: 3-type for the card, and full volume behind it.
    phase_tokens: dict[str, int] | None = None
    phase_tokens_4type: dict[str, int] | None = None
    last_run: WorkItemLastRun | None = None
    running: bool | None = None
    run_started_at: float | None = None
    run_tokens: int | None = None
    run_model: str | None = None
    run_ctx_pct: int | None = None
    run_feature: str | None = None  # the live run's role (triage/plan/…/deputy) — drives the chat verb
    ctx_pct: int | None = None
    tasks: WorkItemTasks | None = None


class TaskItem(BaseModel):
    """One tasks.md checklist line."""
    text: str
    done: bool


class ArtifactCall(BaseModel):
    """One row of a work-item's run call-trail.

    `tool_id` pairs a `result` back to its call, and `parent_tool_id` names the sub-agent spawn the row
    happened inside."""
    id: int
    run_id: int | None = None
    seq: int
    kind: ArtifactKind
    name: str
    description: str | None = None
    tool_id: str | None = None
    parent_tool_id: str | None = None
    created_at: str


# --- route responses ---

class PlanResponse(BaseModel):
    ok: bool
    status: str
    id: str          # the planned work-item's id — named `id` to match every sibling WorkItem*Response
    model: str


class PromptExtractionLink(BaseModel):
    """One captured input page for the last probe's phase runs. Survives the probe's teardown, keyed by
    the dangling item id."""
    run_id: int
    phase: str | None = None
    started_at: str | None = None
    url: str  # /api/dev/work-items/<item>/runs/<run>/input.html?context_id=…


class PromptExtractionStatusResponse(BaseModel):
    """The Prompt X-ray tab's state: a probe in flight (running) + the last probe's captured links."""
    running: bool
    status: str | None = None       # running | done | None (never fired)
    item_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    links: list[PromptExtractionLink] = []


class ArtifactStatusRow(BaseModel):
    """COMPUTED status of one artifact kind — derived at read time from file existence, self-check and
    evidence freshness, so it cannot drift."""
    required: bool
    present: bool
    status: str  # ok | incomplete | missing
    issues: list[str] | None = None
    evidence: dict | None = None  # validation only: {status: passed|stale|failed|unverified, …}


class CheckpointStub(BaseModel):
    """One row of the drilldown's continuity feed (newest first) — full text stays behind path."""
    ts: str
    path: str
    headline: str
    git: str | None = None


class WorkItemDetailResponse(BaseModel):
    item: WorkItem
    plan: str | None = None
    prd: str | None = None
    tasks: list[TaskItem] | None = None
    execution: str | None = None
    artifact_status: dict[str, ArtifactStatusRow] | None = None
    # Raw text of the remaining gate docs; None while a doc has not been written.
    docs: dict[str, str | None] | None = None
    checkpoints: list[CheckpointStub] | None = None


class RunHeader(BaseModel):
    """What one of an item's runs WAS, so a call-trail group can name itself.

    `feature` answers why a run opened with a shell command instead of a phase skill."""
    id: int
    feature: str | None = None
    phase: str | None = None
    status: str | None = None
    model: str | None = None
    tokens: int = 0
    started_at: str | None = None


class WorkItemArtifactsResponse(BaseModel):
    artifacts: list[ArtifactCall]
    runs: list[RunHeader] = []



class TimelineEvent(BaseModel):
    """One event in a run's trail: a prompt, a reply block, a call, or that call's result — the same rows
    the Activity trace shows."""
    id: int
    seq: int
    kind: str                       # prompt | reply | status/tool kinds | result
    name: str | None = None
    description: str | None = None
    tool_id: str | None = None
    parent_tool_id: str | None = None   # the sub-agent spawn this row came from (null = the parent)
    created_at: str


class TimelineRun(BaseModel):
    """One phase agent's run in the unified timeline: which phase/role it was, and its turn events."""
    run_id: int
    phase: str | None = None        # triage | plan | build | vet | review | close
    feature: str | None = None      # chat (interactive owner) | deputy | triage/plan/build/vet/close
    model: str | None = None
    status: str | None = None
    started_at: str | None = None
    events: list[TimelineEvent] = []


class WorkItemTimelineResponse(BaseModel):
    """All of an item's runs oldest-first, each with its ordered events — the read-only conversation the
    panel mirrors across every phase."""
    item_id: str
    runs: list[TimelineRun] = []


class WorkItemGitRecord(BaseModel):
    """The git record a build entry writes onto the item: its branch, worktree dir, and the base it
    branched from."""
    branch: str
    worktree: str
    base: str
    base_sha: str | None = None
    created_at: str | None = None


class WorkItemAdvanceResponse(BaseModel):
    ok: bool
    id: str
    phase: str
    # `from` is a Python keyword → alias it; FastAPI serializes by alias, emitting "from".
    from_: str = Field(alias="from")
    # Present only when this advance ENTERED build for a worktree kind: the record just created.
    git: WorkItemGitRecord | None = None
    # Present only when this advance LEFT review, since the review decision IS the merge. Absent
    # otherwise.
    merge: dict | None = None


class WorkItemSeenResponse(BaseModel):
    """Seen-stamp result. `changed` False means it was already stamped."""
    ok: bool
    id: str
    changed: bool


class WorkItemDocEditResponse(BaseModel):
    """Owner edit of `brief.md` or `plan.md`.

    `saved` False means the text broke the artifact's contract and NOTHING was written; `issues` are
    the lines the gate would refuse on."""
    ok: bool
    id: str
    path: str
    saved: bool
    issues: list[str] = []
    edited_by_owner: str | None = None


class WorkItemAutopilotResponse(BaseModel):
    """Autopilot-toggle result. `changed` False = the flag was already in the requested state."""
    ok: bool
    id: str
    autopilot: bool
    changed: bool


class WorkItemScaffoldResponse(BaseModel):
    """Result of setting a root work-item's anchor pointer (wave OR deliverable)."""
    ok: bool
    id: str
    wave: str | None = None
    deliverable: str | None = None


class PlanBody(BaseModel):
    context_id: str = "global"
    model: str | None = None   # per-run model choice; None -> DEFAULT_RUN_MODEL
    effort: str | None = None  # per-run reasoning effort; None -> item/repo/system default


class AuthorizeBody(BaseModel):
    context_id: str = "global"
    auth_id: str                    # the pending authorization request's id (from authorizations.md)
    decision: str                   # "granted" | "denied"


class ScaffoldBody(BaseModel):
    context_id: str = "global"
    wave: str | None = None         # the roadmap wave this item instances (resolves its deliverable)
    deliverable: str | None = None  # …or a deliverable directly when no wave applies


class DocEditBody(BaseModel):
    context_id: str = "global"
    path: str            # the report's own relative pointer, e.g. "artifacts/plan.md"
    text: str


class AutopilotBody(BaseModel):
    on: bool
