"""Response schemas for the dev meta routes (routers/dev/meta.py): /dev, /dev/log."""

from pydantic import BaseModel, ConfigDict

from .work_items import WorkItem
from .inbox import InboxRow
from ..common import EventScope, EventActor


class GlanceItem(BaseModel):
    """A work-item stub in the glance buckets (in_progress / waiting / blocked)."""
    model_config = ConfigDict(extra="allow")
    id: str
    blocked_by: list[str] | None = None


class Glance(BaseModel):
    """The dashboard glance summary (counts + bucketed item stubs)."""
    model_config = ConfigDict(extra="allow")
    by_status: dict[str, int]
    by_phase: dict[str, int]
    in_progress: list[GlanceItem]
    waiting: list[GlanceItem]
    blocked: list[GlanceItem]
    inbox_open: int
    counts: dict[str, int]


class DevReadResponse(BaseModel):
    """A context's parsed dev knowledge: work-items (enriched) + inbox queue + glance + live runs."""
    root: str
    exists: bool
    work_items: list[WorkItem]
    inbox: list[InboxRow]
    glance: Glance
    context_id: str
    running: list[str]


class DevLogEvent(BaseModel):
    """One activity-log row."""
    id: int
    context_id: str
    scope: EventScope
    item_id: str | None = None
    kind: str  # free label (inbox.add | plan.start | phase.advance | …) — NOT locked
    actor: EventActor
    summary: str
    meta: dict | None = None
    created_at: str


class DevLogResponse(BaseModel):
    context_id: str
    events: list[DevLogEvent]
    count: int
