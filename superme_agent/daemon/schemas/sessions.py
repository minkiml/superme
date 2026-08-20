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
    # True when `item_id` names a work-item whose folder is no longer on disk. The thread is still
    # readable — it happened — but there is nothing left to talk TO, so the composer closes rather
    # than resuming a conversation about work that no longer exists. Always False for a session
    # with no `item_id`: a general chat answers to nobody and is always resumable.
    item_gone: bool = False
    # Durable session KIND (session-kinds-diagnose): 'diagnosis' | 'onboarding' | 'work_item' |
    # 'general' | null. The chat picker derives the category chip from this (+ item_id).
    kind: str | None = None
    # Every thread this row stands for. A work-item channel collapses one thread per phase, and the
    # daemon answers a turn from whichever the phase names — so the id that comes back from a run is
    # often NOT `id`. The rail matches the active session against this list to know it is still on
    # the same channel. A general chat is its own single thread.
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
