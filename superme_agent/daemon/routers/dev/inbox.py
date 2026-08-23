"""Inbox routes — the quick-capture triage queue: /dev/inbox CRUD + push.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ...app_state import (
    DevKnowledgeService, DevStore, SystemSpine, get_dev, get_dev_store, get_spine,
)
from ...deps import dev_root, needs_credential
from ...schemas.dev.inbox import (InboxBody, InboxBriefBody, InboxBriefResponse,
                                  InboxBriefSaveResponse, InboxDeleteResponse, InboxPatch,
                                  InboxPushBody, InboxPushResponse, InboxRow)
from ...services.runs import fire_auto_triage
from ....core import inbox_flow

log = logging.getLogger("superme-agent")

router = APIRouter()


def _role_config(body) -> dict:
    """The four per-role fields off a capture/patch body, in one place so add and patch cannot
    diverge on which of them they carry."""
    return {f"{role}_{k}": getattr(body, f"{role}_{k}")
            for role in ("vet", "deputy") for k in ("model", "effort")}


@router.post("/dev/inbox", response_model=InboxRow)
async def dev_inbox_add(body: InboxBody, dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Quick-capture: add an item to the context's inbox queue (no approval gate)."""
    try:
        row = dev_store.add_inbox(
            body.context_id, body.text, body.kind, body.tag,
            title=body.title, origin=body.origin, spawned_from=body.spawned_from,
            model=body.model, effort=body.effort, autopilot=body.autopilot,
            work_kind=body.work_kind, role_config=_role_config(body),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Log the capture (dev-native — belongs to no work-item).
    dev_store.log_event(
        body.context_id, "inbox.add",
        f"Captured: {(row.get('title') or row.get('text') or '')[:80]}",
        actor=("agent" if "agent" in (row.get("origin") or []) else "owner"),
        meta={"inbox_id": row["id"], "kind": row.get("kind")},
    )
    return row


@router.patch("/dev/inbox/{item_id}", response_model=InboxRow)
async def dev_inbox_update(item_id: int, body: InboxPatch, dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Edit an inbox item: status, kind, tag, text or title.

    A PUSHED row is immutable trace, since its content already moved into the work-item — flipping it
    back to `open` would let a second push mint a duplicate."""
    cur = dev_store.get_inbox(item_id)
    if cur is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    if cur.get("status") == "pushed":
        raise HTTPException(status_code=409, detail="inbox item was pushed — the row is trace now")
    try:
        item = dev_store.update_inbox(
            item_id, status=body.status, kind=body.kind, tag=body.tag,
            text=body.text, title=body.title, routed_to=body.routed_to,
            model=body.model, effort=body.effort, autopilot=body.autopilot,
            work_kind=body.work_kind, **_role_config(body),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    return item


@router.post("/dev/inbox/{item_id}/push", response_model=InboxPushResponse,
             response_model_exclude_unset=True,
             dependencies=[Depends(needs_credential)])
async def dev_inbox_push(item_id: int, body: InboxPushBody,
                         dev_store: DevStore = Depends(get_dev_store),
                         dev: DevKnowledgeService = Depends(get_dev),
                         spine: SystemSpine = Depends(get_spine)) -> dict:
    """Push an inbox item to the workspace — the owner's push.

    One shared transaction: create the work-item at triage, MOVE the inbox content into it as
    `preliminary/` while the row stays as trace, then fire the auto-triage run."""
    rows = dev_store.list_inbox(body.context_id)
    row = next((r for r in rows if r["id"] == item_id), None)
    if row is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    # A note has no work to become, so it has no push. Refused here, not just hidden in the UI.
    if str(row.get("kind")) == "note":
        raise HTTPException(
            status_code=409,
            detail=("This is a note, not an inbox item — notes stay yours and never become work. "
                    "Talk it over in a general session; if it turns out to be work, capture it as "
                    "an item."))
    try:
        wi = inbox_flow.push_inbox_item(dev_store, dev, dev_root(body.context_id), row,
                                        context_id=body.context_id, actor="owner")
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    fire_auto_triage(body.context_id, wi["id"], spine)   # #120 — shared first-kick (services.runs)
    return {"ok": True, "work_item": wi, "inbox": dev_store.get_inbox(item_id),
            "brief_issues": wi.get("brief_issues") or []}


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


@router.get("/dev/inbox/{item_id}/brief", response_model=InboxBriefResponse)
async def dev_inbox_brief(item_id: int, dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """One row's handoff brief. Agent-filed rows carry one from birth; a bare capture has
    none, and `content: null` says so rather than 404-ing — an absent brief is the state the
    owner can still fill, not a missing resource."""
    row = dev_store.get_inbox(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    root = dev_root(row["context_id"])
    content, editable = inbox_flow.read_brief(root, row)
    path, _ = inbox_flow.brief_location(root, row)
    return {"id": item_id, "content": content, "editable": editable, "path": str(path)}


@router.put("/dev/inbox/{item_id}/brief", response_model=InboxBriefSaveResponse)
async def dev_inbox_brief_save(item_id: int, body: InboxBriefBody,
                               dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """Overwrite one open row's handoff brief, creating it when the row never had one. 409 once
    the row is pushed — the brief is the item's provenance from the moment it lands there."""
    row = dev_store.get_inbox(item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    try:
        inbox_flow.write_brief(dev_root(row["context_id"]), row, body.content)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"ok": True, "id": item_id}
