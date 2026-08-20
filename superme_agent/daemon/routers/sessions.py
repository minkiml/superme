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
    """SuperMe's own past CHANNELS for a context, newest first. `mode` (core|dev) scopes.

    ONE ROW PER WORK-ITEM, not per session. An item runs several threads — one per phase, plus the
    headless build/vet pair — but the owner has a single channel to each item: the whole item is
    what they address, and the phase decides which agent answers (`ws.resolve_item_session` picks
    the current phase's thread and redirects any other into it). Listing threads instead of channels
    put an item on screen once per phase under one identical title, and since the body a work-item
    row opens is the item's TIMELINE — every phase, already merged — those rows differed in nothing
    the owner could see or act on. They were duplicates.

    A channel's `id` is an ADDRESS for the item, not a claim about which thread takes the next turn:
    the daemon resolves the talker from the phase when the turn is sent. `message_count` is the
    item's total across its threads; `updated_at` is its most recent word.
    """
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
        # A channel can outlive its item when the folder leaves out of band (a reset, a hand
        # delete). The startup reconciler retires those, but a folder can vanish while the daemon
        # is up — so the surface says so rather than offering a live composer over a thread with
        # nothing behind it. Stated as a FIELD, not inferred from the title falling back to the id,
        # because that fallback is also what a title-less item shows.
        chan["item_gone"] = not item
        # The item's TITLE + its short id (keeps same-titled items apart) — but never over an
        # owner rename. The newest thread's rename is the channel's, since it is the channel.
        if not r.get("has_override"):
            chan["title"] = f"Work-item · {title} · {short_item_id(iid)}"
        channels[iid] = chan
        out.append(chan)
    return out


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
    """Delete a CHANNEL (session-deletion-trace-model) — the row as the owner sees it, so a
    work-item drops all of its phase threads and a general chat drops itself. One tier only: a hard
    delete of the resumable material — spine row AND transcript JSONL on disk — so it leaves the
    picker and can't be reworked. Runs + token trace are PRESERVED (never deleted) and stamped
    `session_fate='deleted'` so the activity log shows the origin session is gone. Irreversible."""
    ctx = contexts.resolve(context_id)
    removed = sessions.delete_channel(ctx, session_id, cause="deleted")
    return {"ok": True, "id": session_id, "purged": bool(removed)}
