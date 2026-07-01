"""Dev meta routes: /dev (parsed knowledge + inbox + glance + run telemetry), /dev/model, /dev/log."""

from fastapi import APIRouter, Depends

from ...app_state import DevKnowledgeService, DevStore, SystemSpine, get_dev, get_dev_store, get_spine
from ...deps import dev_root
from ...schemas.dev.meta import DevReadResponse, DevModelResponse, DevLogResponse

router = APIRouter()


@router.get("/dev", response_model=DevReadResponse, response_model_exclude_unset=True)
async def dev_read(context_id: str = "global",
                   dev: DevKnowledgeService = Depends(get_dev),
                   dev_store: DevStore = Depends(get_dev_store),
                   spine: SystemSpine = Depends(get_spine)) -> dict:
    """A context's parsed dev knowledge (files) + inbox queue (DB) + the glance view.

    `running` lists work-item ids with a headless /plan turn in flight, so the cards can
    show a live "planning…" state while a background agent works.
    """
    root = dev_root(context_id)
    inbox = dev_store.list_inbox(context_id)
    data = dev.read_all(root, inbox=inbox)
    data["context_id"] = context_id
    # Run telemetry comes from the spine (WI-4): one live-rows query + per-item accumulated stats.
    # The daemon queries the spine here, then the service enriches the items (decision #7 push-down).
    live_by_item = {r["item_id"]: r for r in spine.live_runs(context_id) if r.get("item_id")}
    stats = spine.run_stats(context_id, mode="dev")
    dev.enrich_work_items(root, data["work_items"], live_by_item, stats)
    data["running"] = sorted(live_by_item.keys())
    return data


@router.get("/dev/model", response_model=DevModelResponse, response_model_exclude_unset=True)
async def dev_model(context_id: str = "global", dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """The canonical dev-knowledge model manifest (the shared *shape* of dev-knowledge).

    Shared schema, falling back to the global reference home when a context has none."""
    model = dev.read_model(dev_root(context_id), dev_root("global"))
    model["context_id"] = context_id
    return model


@router.get("/dev/log", response_model=DevLogResponse)
async def dev_log(context_id: str = "global", since: str | None = None,
                  until: str | None = None, scope: str | None = None,
                  item_id: str | None = None, limit: int = 200,
                  dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """The activity log — a SELECTIVE read over the events table (PRD §4.9), never a dump.
    Filters: `item_id` (an item's own timeline), `scope` (item|dev|global), `since`/`until`
    (ISO timestamps — e.g. "what happened yesterday"). Newest first. Powers the dashboard
    activity view and the chat "what was done…" queries."""
    events = dev_store.list_events(
        context_id, since=since, until=until, scope=scope, item_id=item_id, limit=limit,
    )
    return {"context_id": context_id, "events": events, "count": len(events)}
