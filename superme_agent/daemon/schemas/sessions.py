"""Response schemas for the sessions routes (sessions.py)."""

from pydantic import BaseModel


class SessionBubble(BaseModel):
    """One replayable chat bubble (`you` | `superme`)."""
    role: str
    text: str


class SessionSummary(BaseModel):
    """A session's picker entry."""
    id: str
    title: str
    surface: str
    mode: str
    updated_at: str
    message_count: int


class SessionDetail(BaseModel):
    """One session's title + its most recent bubbles (older ones skipped server-side)."""
    id: str
    title: str
    updated_at: str
    messages: list[SessionBubble]
    total: int
    truncated: bool


class SessionDeleteResponse(BaseModel):
    ok: bool
    id: str
    purged: bool
