"""Response schemas for the inbox routes (routers/dev/inbox.py)."""

from pydantic import BaseModel

from .work_items import WorkItem
from ..common import InboxKind, InboxStatus, InboxOrigin


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
    source: str | None = None
    created_at: str
    updated_at: str
    title: str | None = None
    origin: InboxOrigin | None = None


class InboxPushResponse(BaseModel):
    ok: bool
    work_item: WorkItem
    inbox: InboxRow


class InboxDeleteResponse(BaseModel):
    ok: bool
    id: int
