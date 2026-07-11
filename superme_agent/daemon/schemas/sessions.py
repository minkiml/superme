"""Response schemas for the sessions routes (sessions.py)."""

from pydantic import BaseModel


class SessionBubble(BaseModel):
    """One replayable chat bubble (`you` | `superme`)."""
    role: str
    text: str


class SessionSummary(BaseModel):
    """A session's picker entry. `item_id`/`item_title` are set when the session is stamped to a
    work-item (work-item-session-recognition-prd) — the chat rail derives its work-item indicator
    from these, so it's correct however the session was opened (card or picker) and clears on switch."""
    id: str
    title: str
    surface: str
    mode: str
    updated_at: str
    message_count: int
    item_id: str | None = None
    item_title: str | None = None


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
