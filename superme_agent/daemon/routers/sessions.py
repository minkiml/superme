"""Sessions routes: list + replay + two-tier removal. History lives in the SDK transcripts."""

from fastapi import APIRouter, Depends, HTTPException

from ..app_state import SessionStore, DevKnowledgeService, get_sessions, get_dev
from ..schemas.sessions import SessionSummary, SessionDetail, SessionDeleteResponse
from ...gateway import contexts

router = APIRouter()


@router.get("/sessions", response_model=list[SessionSummary])
async def sessions_list(context_id: str = "global", mode: str | None = None,
                        sessions: SessionStore = Depends(get_sessions),
                        dev: DevKnowledgeService = Depends(get_dev)) -> list[dict]:
    """SuperMe's own past sessions for a context, newest first. `mode` (core|dev) scopes. A session
    stamped to a work-item carries its `item_id` + resolved `item_title`, so the chat rail can show
    (and clear) the work-item indicator straight from the session, not client-held binding state."""
    ctx = contexts.resolve(context_id, mode or "core")
    rows = sessions.list(ctx, mode=mode)
    # Resolve each work-item session's title once (cache by id — the list is small, localhost).
    if ctx.internal_root:
        dev_root, titles = ctx.internal_root / "dev", {}
        for r in rows:
            iid = r.get("item_id")
            if not iid:
                continue
            if iid not in titles:
                item = dev.read_work_item(dev_root, iid) or {}
                titles[iid] = item.get("title") or iid
            r["item_title"] = titles[iid]
    return rows


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def session_read(session_id: str, context_id: str = "global",
                       sessions: SessionStore = Depends(get_sessions)) -> dict:
    """One session's title + replayable bubble history."""
    data = sessions.read(contexts.resolve(context_id), session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return data


@router.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
async def session_delete(session_id: str, context_id: str = "global",
                         sessions: SessionStore = Depends(get_sessions)) -> dict:
    """Delete a session (session-deletion-trace-model). One tier only: a hard delete of the
    session's resumable material — its spine row AND its transcript JSONL on disk — so it leaves the
    picker and can't be reworked. Its runs + token trace are PRESERVED (never deleted) and stamped
    `session_fate='deleted'` so the activity log shows the origin session is gone. Irreversible."""
    ctx = contexts.resolve(context_id)
    removed = sessions.delete(ctx, session_id, cause="deleted")
    return {"ok": True, "id": session_id, "purged": removed}
