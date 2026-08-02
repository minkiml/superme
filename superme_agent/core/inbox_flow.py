"""Inbox → work-item push flow — the ONE transaction both the FE push route and the agent-side
auto-push (blocking/parallel branch-offs) run (workspace-workflow S3, D3/D5).

Uniform routing: EVERY work-item passes through the inbox (one trace, no two classes of items);
the only variable is who pushes — the owner (FE route; `spawn` waits for this) or the system
(auto-push, immediately after a blocking/parallel branch-off is filed).

The push:
1. creates the work-item at triage/active, carrying the row's `spawned_from` provenance + the
   originating `inbox_id`;
2. MOVES the inbox content folder (`<dev_root>/inbox/<id>/`, the handoff brief + any extra files)
   into the item as `preliminary/` — a move, never a delete: the content survives in the item,
   the inbox ROW stays as trace (status=pushed, routed_to = the item). `preliminary/` is
   immutable after arrival (provenance; corrections go in plan.md);
3. for a `blocking` child: pauses the parent (`awaiting_child` — the D2 typed-awaiting router
   auto-resumes it when the last open blocking child closes);
4. logs the push (+ pause) events.
"""

import logging
import shutil
from pathlib import Path

from .dev_knowledge import DevKnowledgeService

log = logging.getLogger("superme-agent")


def inbox_content_dir(dev_root: Path, inbox_id: int) -> Path:
    """The inbox item's content folder (handoff brief + extras): `<dev_root>/inbox/<id>/`."""
    return Path(dev_root) / "inbox" / str(inbox_id)


def push_inbox_item(store, dev: DevKnowledgeService, dev_root: Path, row: dict, *,
                    context_id: str, actor: str = "owner") -> dict:
    """Run the full push transaction for an OPEN inbox row. Returns the created work-item dict
    ({id, folder}). Raises ValueError on an already-pushed row."""
    if row.get("status") == "pushed":
        raise ValueError("inbox item already pushed")
    inbox_id = row["id"]
    # Idempotency: a crash between create_work_item and push_inbox leaves the row `open` with an
    # orphan item already carrying this inbox_id — a retry must ADOPT that item, never mint a
    # duplicate over the same provenance.
    existing = next((it for it in dev.read_all(dev_root)["work_items"]
                     if str(it.get("inbox_id") or "") == str(inbox_id)), None)
    if existing:
        wi = {"id": str(existing["id"]), "folder": str(existing["id"])}
        log.warning("push retry adopted existing work-item %s for inbox #%s", wi["id"], inbox_id)
    else:
        wi = dev.create_work_item(
            dev_root,
            title=row.get("title") or row.get("text") or "",
            description=row.get("text") or "",
            spawned_from=row.get("spawned_from"),
            inbox_id=inbox_id,
            # The third capture-time policy: whether the item drives its own gates. Set at BIRTH
            # rather than after, so an autopilot item never sits one manual click short of moving.
            autopilot=bool(row.get("autopilot")),
        )
        # F3: the push button LOCKS IN the capture-time run config — model/effort chosen on the
        # inbox row become the work-item's immutable defaults (no per-work-item override after).
        # NULL stays NULL → the item inherits the repo/system default via effective_model.
        if row.get("model"):
            dev.set_work_item_model(dev_root, wi["id"], row["model"])
        if row.get("effort"):
            dev.set_work_item_effort(dev_root, wi["id"], row["effort"])
    # Content folder → the item's preliminary/ (move, not delete; absent for bare FE captures).
    src = inbox_content_dir(dev_root, inbox_id)
    moved = False
    if src.is_dir():
        dst = Path(dev_root) / "work-items" / wi["id"] / "preliminary"
        if dst.exists():
            # A prior partial push already moved it — moving again would NEST src inside dst.
            log.warning("preliminary/ already present for %s; leaving inbox folder in place", wi["id"])
        else:
            shutil.move(str(src), str(dst))
            moved = True
    store.push_inbox(inbox_id, wi["id"])
    store.log_event(
        context_id, "inbox.push",
        f"Pushed to workspace: {row.get('title') or wi['id']}",
        item_id=wi["id"], actor=actor,
        meta={"inbox_id": inbox_id, "preliminary": moved,
              **({"relation": row["spawned_from"]["relation"],
                  "parent": row["spawned_from"]["item"]} if row.get("spawned_from") else {})},
    )
    # A blocking child pauses its parent until the child family closes (D3).
    sf = row.get("spawned_from") or {}
    if sf.get("relation") == "blocking":
        parent_id = str(sf.get("item"))
        parent = dev.read_work_item(dev_root, parent_id)
        if parent and str(parent.get("status")) not in ("done",):
            dev.set_work_item_status(dev_root, parent_id, "awaiting_child")
            store.log_event(
                context_id, "item.pause",
                f"Awaiting blocking child {wi['id']}",
                item_id=parent_id, actor="daemon", meta={"child": wi["id"]},
            )
    return wi
