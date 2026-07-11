"""Inbox routes (the quick-capture triage queue, D-013/D-014): /dev/inbox CRUD + push."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...app_state import DevKnowledgeService, DevStore, get_dev, get_dev_store
from ...deps import dev_root
from ...schemas.dev.inbox import InboxRow, InboxPushResponse, InboxDeleteResponse

router = APIRouter()


class InboxBody(BaseModel):
    text: str
    title: str | None = None  # short headline, entered manually on capture
    kind: str = "note"  # note | idea | todo | question
    tag: str | None = None
    origin: str = "user"  # user (manual) | agent (branch-off proposal)
    context_id: str = "global"


class InboxPatch(BaseModel):
    status: str | None = None  # open | pushed | dropped
    kind: str | None = None
    tag: str | None = None
    text: str | None = None
    title: str | None = None
    routed_to: str | None = None


class InboxPushBody(BaseModel):
    context_id: str = "global"


@router.post("/dev/inbox", response_model=InboxRow)
async def dev_inbox_add(body: InboxBody, dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Quick-capture: add an item to the context's inbox queue (no approval gate)."""
    try:
        row = dev_store.add_inbox(
            body.context_id, body.text, body.kind, body.tag,
            title=body.title, origin=body.origin,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Log the capture (dev-native — belongs to no work-item). PRD §4.9.
    dev_store.log_event(
        body.context_id, "inbox.add",
        f"Captured: {(row.get('title') or row.get('text') or '')[:80]}",
        actor=("agent" if "agent" in (row.get("origin") or []) else "owner"),
        meta={"inbox_id": row["id"], "kind": row.get("kind")},
    )
    return row


@router.patch("/dev/inbox/{item_id}", response_model=InboxRow)
async def dev_inbox_update(item_id: int, body: InboxPatch, dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Edit an inbox item: change status, kind, tag, text, or title."""
    item = dev_store.update_inbox(
        item_id, status=body.status, kind=body.kind, tag=body.tag,
        text=body.text, title=body.title, routed_to=body.routed_to,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    return item


@router.post("/dev/inbox/{item_id}/push", response_model=InboxPushResponse,
             response_model_exclude_unset=True)
async def dev_inbox_push(item_id: int, body: InboxPushBody,
                         dev_store: DevStore = Depends(get_dev_store),
                         dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Push an inbox item to the workspace: stamp a queued work-item, mark the row pushed.

    Agent-free write — creates `work-items/<id>/` at plan_design/queued, seeded from the
    inbox row, and records `routed_to` on the row. Returns the new work-item + the row.
    """
    rows = dev_store.list_inbox(body.context_id)
    row = next((r for r in rows if r["id"] == item_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    if row.get("status") == "pushed":
        raise HTTPException(status_code=409, detail="inbox item already pushed")
    wi = dev.create_work_item(
        dev_root(body.context_id),
        title=row.get("title") or row.get("text") or "",
        description=row.get("text") or "",
        source=row.get("source"),
        inbox_id=item_id,
    )
    updated = dev_store.push_inbox(item_id, wi["id"])
    # The inbox→workspace transition — item-scoped on the new work-item. PRD §4.9.
    dev_store.log_event(
        body.context_id, "inbox.push",
        f"Pushed to workspace: {wi.get('title') or wi['id']}",
        item_id=wi["id"], actor="owner", meta={"inbox_id": item_id},
    )
    return {"ok": True, "work_item": wi, "inbox": updated}


@router.delete("/dev/inbox/{item_id}", response_model=InboxDeleteResponse)
async def dev_inbox_delete(item_id: int, dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Remove an inbox item outright."""
    row = dev_store.get_inbox(item_id)  # capture context + text before the row is gone
    result = dev_store.delete_inbox(item_id)
    if row:
        dev_store.log_event(
            row["context_id"], "inbox.drop",
            f"Dropped inbox item: {(row.get('title') or row.get('text') or '')[:80]}",
            actor="owner", meta={"inbox_id": item_id},
        )
    return result
