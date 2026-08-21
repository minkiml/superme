"""Response schemas for the inbox routes (routers/dev/inbox.py)."""

from pydantic import BaseModel

from .work_items import WorkItem, SpawnedFrom
from ..common import InboxKind, InboxStatus, InboxOrigin, WorkKind


class InboxRow(BaseModel):
    """One quick-capture triage-queue row. kind/status/origin are locked: DevStore validates
    them against its own value-sets on write, so the Literal can't 500 on a real row."""
    id: int
    context_id: str
    kind: InboxKind
    text: str
    tag: str | None = None
    status: InboxStatus
    routed_to: str | None = None
    created_at: str
    updated_at: str
    title: str | None = None
    origin: list[InboxOrigin] = []  # an item can accrue multiple origins (e.g. ['user','agent'])
    # The provenance edge a branch-off row carries before push (copied onto the work-item on push).
    spawned_from: SpawnedFrom | None = None
    # Run config chosen at capture, locked into the work-item at push. NULL = inherit default.
    model: str | None = None
    effort: str | None = None
    # Decided here because the work-item route only accepts the flag pre-build. On by default; the
    # card's toggle is the opt-out.
    autopilot: bool = True
    # Which machinery this row becomes when pushed. NULL means nobody has judged, and triage
    # decides alone.
    work_kind: WorkKind | None = None
    # Roles that run on their own tier, falling through to the repo or system default — never to
    # `model` above.
    vet_model: str | None = None
    vet_effort: str | None = None
    deputy_model: str | None = None
    deputy_effort: str | None = None


class InboxPushResponse(BaseModel):
    ok: bool
    work_item: WorkItem
    inbox: InboxRow
    # The brief self-check, at the last moment it is actionable. Never blocks the push; it says
    # how cold triage starts.
    brief_issues: list[str] = []


class InboxDeleteResponse(BaseModel):
    ok: bool
    id: int


class InboxBriefResponse(BaseModel):
    """One row's handoff brief — the cold-start context the item it becomes reads first.

    `content` is null when none was filed, which is legal. `editable` is false once pushed: the brief
    has become provenance."""
    id: int
    content: str | None = None
    editable: bool
    path: str


class InboxBriefBody(BaseModel):
    content: str


class InboxBriefSaveResponse(BaseModel):
    ok: bool
    id: int
