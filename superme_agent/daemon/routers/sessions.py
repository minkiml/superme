"""Sessions routes: list + replay + two-tier removal. History lives in the SDK transcripts."""

from fastapi import APIRouter, Depends, HTTPException

from ..app_state import SessionStore, get_sessions
from ..schemas.sessions import SessionSummary, SessionDetail, SessionDeleteResponse
from ...gateway import contexts

router = APIRouter()


@router.get("/sessions", response_model=list[SessionSummary])
async def sessions_list(context_id: str = "global", mode: str | None = None,
                        sessions: SessionStore = Depends(get_sessions)) -> list[dict]:
    """SuperMe's own past sessions for a context, newest first. `mode` (core|dev) scopes."""
    return sessions.list(contexts.resolve(context_id, mode or "core"), mode=mode)


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def session_read(session_id: str, context_id: str = "global",
                       sessions: SessionStore = Depends(get_sessions)) -> dict:
    """One session's title + replayable bubble history."""
    data = sessions.read(contexts.resolve(context_id), session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return data


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def session_delete(session_id: str, context_id: str = "global", purge: bool = False,
                         sessions: SessionStore = Depends(get_sessions)) -> dict:
    """Remove a session. Two tiers (the owner chooses):
      • forget (default) — drop it from the picker only; the transcript JSONL is LEFT on disk, so
        it can still be referenced/recovered. This is the reversible "tidy my list" action.
      • purge (`?purge=true`) — ALSO delete the transcript JSONL from disk. Irreversible disk-level
        cleanup; leaves no trace (PRD §4.9 hygiene)."""
    ctx = contexts.resolve(context_id)
    if purge:
        removed = sessions.purge(ctx, session_id)
        return {"ok": True, "id": session_id, "purged": removed}
    sessions.forget(ctx, session_id)
    return {"ok": True, "id": session_id, "purged": False}
