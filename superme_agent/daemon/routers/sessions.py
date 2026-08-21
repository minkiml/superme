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
    """SuperMe's own past CHANNELS for a context, newest first.

    ONE ROW PER WORK-ITEM, not per session: the owner addresses the ITEM, and the phase decides which
    agent answers. A channel's `id` is an ADDRESS."""
    ctx = contexts.resolve(context_id, mode or "core")
    rows = sessions.list(ctx, mode=mode)
    if not ctx.internal_root:
        return rows
    dev_root = ctx.internal_root / "dev"
    out: list[dict] = []
    channels: dict[str, dict] = {}
    for r in rows:
        iid = r.get("item_id")
        if not iid:
            r["thread_ids"] = [r["id"]]
            out.append(r)
            continue
        chan = channels.get(iid)
        if chan is not None:      # another thread of an item already on screen — fold it in
            chan["thread_ids"].append(r["id"])
            chan["message_count"] += r.get("message_count") or 0
            if (r.get("updated_at") or "") > (chan.get("updated_at") or ""):
                chan["updated_at"] = r["updated_at"]
            continue
        item = dev.read_work_item(dev_root, iid) or {}
        title = item.get("title") or iid
        chan = dict(r)
        chan["thread_ids"] = [r["id"]]
        chan["item_title"] = title
        # A channel can outlive its item, so say so rather than offer a composer over nothing.
        chan["item_gone"] = not item
        # The item's title and short id, but never over an owner rename. The newest thread's
        # rename is the channel's.
        if not r.get("has_override"):
            chan["title"] = f"Work-item · {title} · {short_item_id(iid)}"
        channels[iid] = chan
        out.append(chan)
    return out


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def session_read(session_id: str, context_id: str = "global", limit: int = 10,
                       sessions: SessionStore = Depends(get_sessions)) -> dict:
    """One session's title and its most recent `limit` replayable bubbles.

    `limit<=0` is the whole transcript. The agent always resumes with full server-side context
    regardless."""
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
    """Delete a CHANNEL — the row as the owner sees it, so a work-item drops all its phase threads.

    One tier only: a hard delete of the resumable material. Runs and token trace are PRESERVED."""
    ctx = contexts.resolve(context_id)
    removed = sessions.delete_channel(ctx, session_id, cause="deleted")
    return {"ok": True, "id": session_id, "purged": bool(removed)}
