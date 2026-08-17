"""Response schemas for the inbox routes (routers/dev/inbox.py)."""

from pydantic import BaseModel

from .work_items import WorkItem, SpawnedFrom
from ..common import InboxKind, InboxStatus, InboxOrigin, WorkKind


class InboxRow(BaseModel):
    """One quick-capture triage-queue row. kind/status/origin are locked (R5): DevStore validates
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
    # D3 provenance a branch-off row carries before push (copied onto the work-item on push).
    spawned_from: SpawnedFrom | None = None
    # F3: run config chosen at capture, locked into the work-item at push. NULL = inherit default.
    model: str | None = None
    effort: str | None = None
    # Whether the work-item this row becomes drives its own gates. Decided here because the
    # work-item route only accepts the flag pre-build — capture is the moment always in time.
    # ON by default: driving itself is the normal case, and the card's toggle is the opt-out.
    autopilot: bool = True
    # §4.1: which machinery this row becomes when pushed (implementation | research). NULL = nobody
    # has judged yet, and triage decides alone — the behaviour every row had before the field.
    work_kind: WorkKind | None = None


class InboxPushResponse(BaseModel):
    ok: bool
    work_item: WorkItem
    inbox: InboxRow
    # D5's brief self-check, run at the last moment it is actionable: the brief has just moved into
    # the item's read-only `preliminary/`. Empty = fine. Never blocks the push — a bare capture is
    # legal — it says how cold triage is about to start.
    brief_issues: list[str] = []


class InboxDeleteResponse(BaseModel):
    ok: bool
    id: int
