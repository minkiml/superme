"""Dev meta routes: /dev (parsed knowledge + inbox + glance + run telemetry), /dev/log,
/dev/workgraph (the derived graph projection)."""

from fastapi import APIRouter, Depends

from ...app_state import DevKnowledgeService, DevStore, SystemSpine, get_dev, get_dev_store, get_spine
from ...deps import dev_root
from ...schemas.dev.meta import (
    AttentionResponse, DevReadResponse, DevLogResponse, WorkGraphResponse,
)
from ....core import artifacts, attention, workgraph
from ....core.dev_knowledge import _parse_deliverables, _parse_waves

router = APIRouter()


@router.get("/dev", response_model=DevReadResponse, response_model_exclude_unset=True)
async def dev_read(context_id: str = "global",
                   dev: DevKnowledgeService = Depends(get_dev),
                   dev_store: DevStore = Depends(get_dev_store),
                   spine: SystemSpine = Depends(get_spine)) -> dict:
    """A context's parsed dev knowledge (files) + inbox queue (DB) + the glance view.

    `running` lists work-item ids with a background /plan turn in flight, so the cards can
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


@router.get("/dev/attention", response_model=AttentionResponse)
async def dev_attention(context_id: str = "global",
                        dev: DevKnowledgeService = Depends(get_dev),
                        spine: SystemSpine = Depends(get_spine)) -> dict:
    """The attention engine (S7/D10): every item in at most one bucket, strict priority
    needs_you (awaiting_human) > deputy_working (live deputy judgment) > running (live phase run)
    > unread (terminal, never opened) — derived from durable state at read time. `badge` = the top
    non-empty tier's color + count, or null when nothing claims attention. Powers the workspace
    badge + kanban tinting."""
    root = dev_root(context_id)
    items = dev.read_all(root)["work_items"]
    live = {r["item_id"]: r for r in spine.live_runs(context_id) if r.get("item_id")}
    running = set(live)
    # The deputy subset: live runs stamped feature="deputy" (its judgment is standing in for the
    # owner, so it gets its own attention tier, not the generic green "running") — F1.
    deputy = {iid for iid, r in live.items() if str(r.get("feature")) == "deputy"}
    # How many proposals in each parked research review are waiting on a ruling only the owner can
    # give. Read here rather than inside `assign` so that stays pure — and read at ALL, because the
    # bare "at the review gate" line is identical whether or not the item is asking them something.
    rulings = {}
    for it in items:
        if str(it.get("status")) == "awaiting_human" and str(it.get("phase")) == "review":
            try:
                _f, held = artifacts.filed_and_withheld(
                    artifacts.research_proposals(root / "work-items" / str(it.get("id"))))
                if held:
                    rulings[str(it.get("id"))] = len(held)
            except Exception:
                pass
    return {"context_id": context_id,
            **attention.assign(items, running, deputy, rulings_by_item=rulings)}


@router.get("/dev/workgraph", response_model=WorkGraphResponse)
async def dev_workgraph(context_id: str = "global",
                        dev: DevKnowledgeService = Depends(get_dev),
                        dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """The DERIVED WorkGraph projection (D3): repo root · roadmap deliverables · work-items ·
    spawned-but-unpushed inbox rows, with contains / spawned_from(relation) / supersedes edges.
    Assembled on demand from the authoritative feeds — nothing stored, nothing to sync. Cycles
    (a hand-edited provenance loop) are REPORTED as data, never a 500. Git state joins as node
    decoration in S4; the graph VIEW consumes this in S7."""
    root = dev_root(context_id)
    items = dev.read_all(root)["work_items"]
    g = workgraph.build(
        repo_id=context_id,
        items=items,
        deliverables=_parse_deliverables(dev.read_general_doc(root, "project-prd") or ""),
        waves=_parse_waves(dev.read_general_doc(root, "roadmap") or ""),
        inbox_rows=dev_store.list_inbox(context_id),
    )
    return {"context_id": context_id, "nodes": list(g.nodes.values()), "edges": g.edges,
            "cycles": g.cycles(), "topo": g.topo()}


@router.get("/dev/log", response_model=DevLogResponse)
async def dev_log(context_id: str = "global", since: str | None = None,
                  until: str | None = None, scope: str | None = None,
                  item_id: str | None = None, limit: int = 200,
                  dev_store: DevStore = Depends(get_dev_store)) -> dict:
    """The activity log — a SELECTIVE read over the events table (PRD §4.9), never a dump.
    Filters: `item_id` (an item's own timeline), `scope`, `since`/`until` (ISO timestamps — e.g.
    "what happened yesterday"). Newest first. Powers the dashboard activity view and the chat
    "what was done…" queries.

    `scope` takes a stored scope (`item` | `dev`) or **`repo`** — the activity view's read: every
    dev-native row plus the item-scoped kinds that are milestones of the PROJECT rather than steps
    inside one item (`REPO_MILESTONE_KINDS`). Per-item traces belong to that item's drilldown.

    Discarded rows (a re-run's soft-deleted attempt) follow the reader split: an ITEM read is the
    drilldown asking "what is happening on this item", so it sees the current attempt only; a
    REPO-WIDE read is the project's history, which includes the attempts that were thrown away."""
    events = dev_store.list_events(
        context_id, since=since, until=until, scope=scope, item_id=item_id, limit=limit,
        include_discarded=item_id is None,
    )
    return {"context_id": context_id, "events": events, "count": len(events)}
