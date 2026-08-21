"""Response schemas for the sessions routes (sessions.py)."""

from pydantic import BaseModel


class SessionBubble(BaseModel):
    """One replayable chat bubble (`you` | `superme`)."""
    role: str
    text: str


class SessionSummary(BaseModel):
    """A session's picker entry.

    `item_id` and `item_title` are set when the session is stamped to a work-item, so the chat rail's
    indicator is correct however the session was opened."""
    id: str
    title: str
    surface: str
    mode: str
    updated_at: str
    message_count: int
    item_id: str | None = None
    item_title: str | None = None
    # The thread is still readable, but there is nothing left to talk TO, so the composer closes.
    item_gone: bool = False
    # The durable session kind. The chat picker derives its category chip from this and `item_id`.
    kind: str | None = None
    # A work-item channel collapses one thread per phase, so the id a run returns is often NOT
    # `id`.
    thread_ids: list[str] = []


class SessionDetail(BaseModel):
    """One session's title + its most recent bubbles (older ones skipped server-side)."""
    id: str
    title: str
    updated_at: str
    messages: list[SessionBubble]
    total: int
    truncated: bool


class SessionRenameBody(BaseModel):
    """Owner rename of a session. `title` blank/empty ⇒ clear the override (revert to derived)."""
    context_id: str = "global"
    title: str | None = None


class SessionRenameResponse(BaseModel):
    ok: bool
    id: str
    title: str  # the effective title after the change (may be the re-derived one on clear)


class SessionDeleteResponse(BaseModel):
    ok: bool
    id: str
    purged: bool
