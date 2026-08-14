"""Sessions routes: list + replay + two-tier removal. History lives in the SDK transcripts."""

from fastapi import APIRouter, Depends, HTTPException

from ..app_state import SessionStore, DevKnowledgeService, get_sessions, get_dev
from ..schemas.sessions import (
    SessionSummary, SessionDetail, SessionDeleteResponse, SessionRenameBody, SessionRenameResponse,
)
from ...gateway import contexts
from ...core.sessions import short_item_id

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
        dev_root, titles, gone = ctx.internal_root / "dev", {}, {}
        for r in rows:
            iid = r.get("item_id")
            if not iid:
                continue
            if iid not in titles:
                item = dev.read_work_item(dev_root, iid) or {}
                titles[iid] = item.get("title") or iid
                gone[iid] = not item
            r["item_title"] = titles[iid]
            # A session can outlive its item when the folder leaves out of band (a reset, a hand
            # delete). The startup reconciler retires those, but a folder can vanish while the
            # daemon is up — so the surface says so rather than offering a live composer over a
            # thread with nothing behind it. Stated as a FIELD, not inferred from the title
            # falling back to the id, because that fallback is also what a title-less item shows.
            r["item_gone"] = gone[iid]
            # Upgrade the preset to the item's TITLE + its short id (keeps same-titled items apart) —
            # but never over an owner rename.
            if not r.get("has_override"):
                r["title"] = f"Work-item · {titles[iid]} · {short_item_id(iid)}"
    return rows


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def session_read(session_id: str, context_id: str = "global", limit: int = 10,
                       sessions: SessionStore = Depends(get_sessions)) -> dict:
    """One session's title + its most recent `limit` replayable bubbles (older ones skipped). The
    chat's "See more" grows `limit` in steps of 10 to reveal older messages; `limit<=0` = the whole
    transcript. The agent always resumes with full server-side context regardless."""
    data = sessions.read(contexts.resolve(context_id), session_id, limit=limit)
    if data is None:
        raise HTTPException(status_code=404, detail="session not found")
    return data


@router.patch("/sessions/{session_id}", response_model=SessionRenameResponse)
async def session_rename(session_id: str, body: SessionRenameBody,
                         sessions: SessionStore = Depends(get_sessions)) -> dict:
    """Set (or clear) a session's owner TITLE override. A blank title reverts to the transcript-
    derived title. The override is stored on the spine session row and wins in list/read."""
    ctx = contexts.resolve(body.context_id)
    if not sessions.rename(ctx, session_id, body.title):
        raise HTTPException(status_code=404, detail="session not found")
    clean = (body.title or "").strip()
    if clean:
        title = clean
    else:  # cleared → re-derive the effective title from the transcript
        d = sessions.read(ctx, session_id, limit=1)
        title = d["title"] if d else session_id
    return {"ok": True, "id": session_id, "title": title}


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
