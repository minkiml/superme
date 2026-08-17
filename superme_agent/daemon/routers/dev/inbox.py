"""Inbox routes (the quick-capture triage queue, D-013/D-014): /dev/inbox CRUD + push."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...app_state import (
    DevKnowledgeService, DevStore, SystemSpine, get_dev, get_dev_store, get_spine,
)
from ...deps import dev_root
from ...schemas.dev.inbox import InboxRow, InboxPushResponse, InboxDeleteResponse
from ...services.runs import fire_auto_triage
from ....core import inbox_flow

log = logging.getLogger("superme-agent")

router = APIRouter()


class InboxBody(BaseModel):
    text: str
    title: str | None = None  # short headline, entered manually on capture
    kind: str = "note"  # note | idea | todo | question
    tag: str | None = None
    origin: str = "user"  # user (manual) | agent (branch-off proposal)
    context_id: str = "global"
    # D3 provenance a branch-off row carries from birth: {item, relation: blocking|parallel|spawn}.
    spawned_from: dict | None = None
    # F3: run config chosen at capture — locked into the work-item at push. NULL = inherit default.
    model: str | None = None
    effort: str | None = None
    autopilot: bool = True     # drives its own gates after push; the card's toggle opts out
    # §4.1: the PROPOSED work-item kind (implementation | research). None = undecided, which is
    # what every capture was before the field — triage then judges alone.
    work_kind: str | None = None


class InboxPatch(BaseModel):
    status: str | None = None  # open | pushed  (no 'dropped' state — dropping an inbox item is a hard delete)
    kind: str | None = None
    tag: str | None = None
    text: str | None = None
    title: str | None = None
    routed_to: str | None = None
    # The per-item config, editable for as long as the row is open (push freezes all three).
    model: str | None = None
    effort: str | None = None
    autopilot: bool | None = None
    # "" clears it back to undecided; None means the caller didn't touch the field.
    work_kind: str | None = None


class InboxPushBody(BaseModel):
    context_id: str = "global"


@router.post("/dev/inbox", response_model=InboxRow)
async def dev_inbox_add(body: InboxBody, dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Quick-capture: add an item to the context's inbox queue (no approval gate)."""
    try:
        row = dev_store.add_inbox(
            body.context_id, body.text, body.kind, body.tag,
            title=body.title, origin=body.origin, spawned_from=body.spawned_from,
            model=body.model, effort=body.effort, autopilot=body.autopilot,
            work_kind=body.work_kind,
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
    """Edit an inbox item: change status, kind, tag, text, or title. A PUSHED row is immutable
    trace (its content already moved into the work-item's preliminary/): flipping it back to
    `open` would let a second push mint a duplicate work-item over the same provenance."""
    cur = dev_store.get_inbox(item_id)
    if cur is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    if cur.get("status") == "pushed":
        raise HTTPException(status_code=409, detail="inbox item was pushed — the row is trace now")
    item = dev_store.update_inbox(
        item_id, status=body.status, kind=body.kind, tag=body.tag,
        text=body.text, title=body.title, routed_to=body.routed_to,
        model=body.model, effort=body.effort, autopilot=body.autopilot,
        work_kind=body.work_kind,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    return item


@router.post("/dev/inbox/{item_id}/push", response_model=InboxPushResponse,
             response_model_exclude_unset=True)
async def dev_inbox_push(item_id: int, body: InboxPushBody,
                         dev_store: DevStore = Depends(get_dev_store),
                         dev: DevKnowledgeService = Depends(get_dev),
                         spine: SystemSpine = Depends(get_spine)) -> dict:
    """Push an inbox item to the workspace — the owner's push (the `spawn` relation waits for
    exactly this; blocking/parallel children auto-pushed at branch-off time never reach here
    open). One shared transaction (core/inbox_flow): creates `work-items/<id>/` at triage/active
    carrying `spawned_from` + `inbox_id`, MOVES the inbox content folder (handoff brief + extras)
    into the item as `preliminary/` (the row stays as trace), and pauses the parent when the
    relation is blocking. Then fires the auto-triage run (#120 — no manual trigger). Returns the
    new work-item + the row.
    """
    rows = dev_store.list_inbox(body.context_id)
    row = next((r for r in rows if r["id"] == item_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    try:
        wi = inbox_flow.push_inbox_item(dev_store, dev, dev_root(body.context_id), row,
                                        context_id=body.context_id, actor="owner")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    fire_auto_triage(body.context_id, wi["id"], spine)   # #120 — shared first-kick (services.runs)
    return {"ok": True, "work_item": wi, "inbox": dev_store.get_inbox(item_id)}


@router.delete("/dev/inbox/{item_id}", response_model=InboxDeleteResponse)
async def dev_inbox_delete(item_id: int, dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Remove an inbox item outright (drop = hard delete), including its content folder — unless
    the row was pushed (the folder already moved into the work-item's preliminary/)."""
    import shutil
    row = dev_store.get_inbox(item_id)  # capture context + text before the row is gone
    if row and row.get("status") != "pushed":
        folder = inbox_flow.inbox_content_dir(dev_root(row["context_id"]), item_id)
        if folder.is_dir():
            shutil.rmtree(folder)
    result = dev_store.delete_inbox(item_id)
    if row:
        dev_store.log_event(
            row["context_id"], "inbox.drop",
            f"Dropped inbox item: {(row.get('title') or row.get('text') or '')[:80]}",
            actor="owner", meta={"inbox_id": item_id},
        )
    return result
