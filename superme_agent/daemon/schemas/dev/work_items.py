"""Response schemas for the dev work-item routes (routers/dev/work_items.py).

The `WorkItem` model covers BOTH the 14-field base shape (read_work_item / create_work_item, used by
the detail + inbox-push routes) and the 28-field enriched shape (the /dev list: base + tree-walk +
run telemetry). All non-core fields are optional and routes use `response_model_exclude_unset=True`,
so each route emits exactly the keys it actually returns (no spurious nulls, no dropped fields).
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from ..common import WorkKind, WorkPhase, WorkStatus, WorkOutcome, SpawnRelation, ArtifactKind


class SpawnedFrom(BaseModel):
    """The D3 provenance edge, child-side: which item this one branched off and how. `blocking`/
    `parallel` = real children (gate the parent's completion; blocking also pauses it);
    `spawn` = provenance-only follow-up. Exactly one origin edge per item; parent views derived."""
    item: str
    relation: SpawnRelation
    note: str | None = None


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
    ctx_pct: int | None = None


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
    # Workspace-workflow S1 contract fields: `kind` picks the machinery (KIND_PROFILES; null on
    # pre-workflow items reads as implementation), `spawned_from` is the branch-off provenance
    # edge, `outcome` stamps HOW the item ended (with status=done), `superseded_by` backs the
    # superseded outcome, `inbox_id` is the originating inbox row (D5 trace).
    kind: WorkKind | None = None
    spawned_from: SpawnedFrom | None = None
    superseded_by: str | None = None
    outcome: WorkOutcome | None = None
    inbox_id: int | None = None
    phase: WorkPhase | None = None
    status: WorkStatus | None = None
    # Peer sequencing (autopilot slice 1): ids this item may not start before. Present when set;
    # an item with an open upstream rests at status `awaiting_upstream` until the scheduler releases.
    after: list[str] | None = None
    # Autopilot (slice 2): the per-item policy — does the workflow drive its gates without a click.
    autopilot: bool | None = None
    model: str | None = None
    effort: str | None = None  # configured reasoning effort (low|medium|high) its runs use
    # work-item frontmatter dates parse to datetime.date (YAML); a date|str union keeps them faithful
    # (date → isoformat "YYYY-MM-DD" on serialize, exactly as the raw jsonable_encoder path did).
    done_at: date | str | None = None
    # --- git record (workspace-workflow S4/D4) --- written at build entry (branch + worktree +
    # base) and at the review merge (merge commit + backup ref). A terminal item KEEPS the record
    # minus the removed dir (the branch ref is trace — never deleted).
    git_branch: str | None = None
    git_worktree: str | None = None
    git_base: str | None = None
    git_merge_commit: str | None = None
    git_merged_at: str | None = None
    git_backup_ref: str | None = None
    # `strict` repos only (renovation §2.2): when the deputy approved and handed the merge to the
    # owner. Set ∧ no merge commit = the PR is open (the FE derives it the same way the BE does).
    git_pr_opened_at: str | None = None
    # S7 attention engine: the owner-opened read receipt — a terminal item without it sits in
    # the `unread` bucket. Never bumps updated_at. YAML round-trips the stamp as datetime, so
    # the union keeps it faithful (datetime → isoformat on serialize), same as done_at.
    seen_at: datetime | str | None = None
    # Set once the item's loose artifact files were folded into `archive.zip` (the on-demand
    # folder archive). A STORAGE fact, not a lifecycle state — the item stays done/completed.
    archived_at: datetime | str | None = None
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
    # Per-phase token accumulation {phase → Σ}, both bases: `phase_tokens` = 3-type (what the card
    # shows for its current phase), `phase_tokens_4type` = full volume (3-type + cache_read) behind it.
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
    """One row of a work-item's run call-trail (tool / sub-agent / skill invocation, or its result).
    `tool_id` pairs a `result` row back to its call (concurrent tools return out of order)."""
    id: int
    run_id: int | None = None
    seq: int
    kind: ArtifactKind
    name: str
    description: str | None = None
    tool_id: str | None = None
    created_at: str


# --- route responses ---

class PlanResponse(BaseModel):
    ok: bool
    status: str
    id: str          # the planned work-item's id — named `id` to match every sibling WorkItem*Response
    model: str


class WorkItemDeleteResponse(BaseModel):
    ok: bool
    id: str
    session_cleared: bool
    inbox_removed: int | None = None


class PromptExtractionLink(BaseModel):
    """One captured "A" page for the last Prompt X-ray probe's phase runs — survives the probe's
    teardown (run_input rows are kept trace, keyed by the dangling item_id)."""
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
    """COMPUTED status of one artifact kind (S2 — derived from file existence + self-check +
    evidence freshness at read time; never stored in any doc, so it cannot drift)."""
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
    # S7 drilldown: raw text of the remaining gate docs (validation/readiness/investigation/closeout;
    # value None while un-emitted) + the checkpoint continuity feed.
    docs: dict[str, str | None] | None = None
    checkpoints: list[CheckpointStub] | None = None


class WorkItemArtifactsResponse(BaseModel):
    artifacts: list[ArtifactCall]


class WorkItemArchiveResponse(BaseModel):
    """The on-demand folder archive of a DONE item — storage only, never a lifecycle change."""
    ok: bool
    id: str
    archive: str          # the archive file, relative to the item folder
    files: int            # how many loose files were folded in (0 on a repeat call)
    already: bool = False # this item was already archived; nothing changed


class TimelineEvent(BaseModel):
    """One event in a run's trail (F2 timeline): a prompt, an assistant reply block, a tool/skill
    call (status), or that call's result — the same rows the Activity trace shows, per run."""
    id: int
    seq: int
    kind: str                       # prompt | reply | status/tool kinds | result
    name: str | None = None
    description: str | None = None
    tool_id: str | None = None
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
    """The F2 unified timeline: all of an item's runs oldest-first, each with its ordered events —
    the read-only conversation the panel mirrors across every phase."""
    item_id: str
    runs: list[TimelineRun] = []


class WorkItemGitRecord(BaseModel):
    """The git record a build entry writes onto the item (S4): its branch, worktree dir, and the
    base it branched from (the trunk, or the parent's branch for a blocking child)."""
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
    # Present only when this advance LEFT review (the review decision IS the merge, B2): the merge
    # result — target/path, knowledge ops applied, freshness lint. Absent on every other transition.
    merge: dict | None = None


class WorkItemSeenResponse(BaseModel):
    """Seen-stamp result (S7 read receipt). `changed` False = was already stamped just now."""
    ok: bool
    id: str
    changed: bool


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
