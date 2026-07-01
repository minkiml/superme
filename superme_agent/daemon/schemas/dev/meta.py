"""Response schemas for the dev meta routes (routers/dev/meta.py): /dev, /dev/model, /dev/log."""

from pydantic import BaseModel, ConfigDict, Field

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


class ManifestEntity(BaseModel):
    """One model.yaml entity (the dev-knowledge schema's nodes). Lenient (extra='allow', all
    optional) so a richer/older manifest never 500s, but typing `entities` to this validates each
    row IS a structured object — a malformed entry (e.g. a bare string) becomes a clear server-side
    error at request time rather than a silent FE render crash (R5)."""
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    label: str | None = None
    store: str | None = None
    storage: str | None = None
    schema_: list | None = Field(default=None, alias="schema")
    summary: str | None = None
    fields: list | None = None


class DevModelResponse(BaseModel):
    """The dev-knowledge model manifest (model.yaml) + envelope. The manifest body is dynamic YAML,
    so the known sections are declared for documentation and extra='allow' carries anything else;
    a partial manifest validates too (sections optional, route uses exclude_unset). `entities` is
    validated as structured rows (R5); the looser sections stay dict-typed (documented stretch)."""
    model_config = ConfigDict(extra="allow")
    version: int | None = None
    entities: list[ManifestEntity] | None = None
    lifecycle: dict | None = None
    knowledge_layers: dict | None = None
    operating_model: dict | None = None
    context_stack: dict | None = None
    skills: dict | None = None
    exists: bool
    context_id: str


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
