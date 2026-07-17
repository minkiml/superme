"""Response schemas for the dev meta routes (routers/dev/meta.py): /dev, /dev/log."""

from pydantic import BaseModel, ConfigDict

from .work_items import WorkItem
from .inbox import InboxRow
from ..common import EventScope, EventActor


class GlanceItem(BaseModel):
    """A work-item stub in the glance buckets (active / awaiting_human)."""
    model_config = ConfigDict(extra="allow")
    id: str


class Glance(BaseModel):
    """The dashboard glance summary (counts + bucketed item stubs). `awaiting_human` is the
    attention bucket (D10: the only status that pages the owner)."""
    model_config = ConfigDict(extra="allow")
    by_status: dict[str, int]
    by_phase: dict[str, int]
    active: list[GlanceItem]
    awaiting_human: list[GlanceItem]
    inbox_open: int
    counts: dict[str, int]


class AttentionRow(BaseModel):
    """One item's attention claim: which bucket, why (one human line), and the gate it sits at
    (needs_you only)."""
    id: str
    title: str
    kind: str
    phase: str | None = None
    status: str | None = None
    outcome: str | None = None
    bucket: str          # needs_you | running | unread
    reason: str
    gate: str | None = None


class AttentionBadge(BaseModel):
    """The global badge: the TOP non-empty tier only — one color, one count (D10)."""
    tier: str            # needs_you | running | unread
    color: str           # orange | green | blue
    count: int


class AttentionResponse(BaseModel):
    """The attention engine's read: strict-priority buckets derived from durable state."""
    context_id: str
    buckets: dict[str, list[AttentionRow]]
    badge: AttentionBadge | None = None


class DevReadResponse(BaseModel):
    """A context's parsed dev knowledge: work-items (enriched) + inbox queue + glance + live runs."""
    root: str
    exists: bool
    work_items: list[WorkItem]
    inbox: list[InboxRow]
    glance: Glance
    context_id: str
    running: list[str]


class WorkGraphNode(BaseModel):
    """One node of the derived WorkGraph (D3). `kind` ∈ repo_root | deliverable | work_item |
    inbox_spawn; work_item nodes carry item_kind/phase/status/outcome (+ git decoration in S4)."""
    model_config = ConfigDict(extra="allow")
    id: str
    kind: str
    label: str | None = None


class WorkGraphEdge(BaseModel):
    """One typed edge: contains | spawned_from (carries `relation`) | supersedes."""
    model_config = ConfigDict(extra="allow")
    src: str
    dst: str
    kind: str


class WorkGraphResponse(BaseModel):
    context_id: str
    nodes: list[WorkGraphNode]
    edges: list[WorkGraphEdge]
    cycles: list[list[str]]  # non-empty = a provenance loop to surface (never a 500)
    topo: list[str] | None = None  # topological order; None when cyclic


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
