"""Peer scheduler — the `after:` edge's release hook.

`core/status_router.py` decides WHO is released (pure, over item dicts); this module performs the
writes and logs the events. It exists as one function rather than four inlined blocks because a
work-item can go terminal down four different paths — complete, abandon/supersede, hard delete, and
the startup reconcile — and a peer parked at `awaiting_upstream` that only some of them release is
work that silently never runs.

Called AFTER the terminal write, with `items` already carrying the terminal item's new state (the
caller patches its own in-memory copy — same convention the parent-resume blocks use).
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...core import status_router

log = logging.getLogger("superme-agent")


def release_downstream(dev, dev_root: Path, dev_store, context_id: str,
                       items: list[dict], upstream_id: str, *, cause: str) -> dict:
    """Fire the peers waiting on `upstream_id`. Returns `{released, paged}` (both id lists).

    - **released** — every upstream completed, so the item goes `active` and the normal scheduler
      picks it up. No human involved: this is the whole point of the edge.
    - **paged** — an upstream ended abandoned or superseded. The wait is now pointless and nothing
      will release it on its own, so the item goes `awaiting_human` with the dead upstream named.
      Auto-starting instead would build against a predecessor that never landed; leaving it parked
      would hide it forever. Paging is the only honest third option.

    Best-effort per item: one bad write must not strand the rest of the cohort.
    """
    released, paged = status_router.items_to_release(items, str(upstream_id))
    autopilot = {str(it.get("id")): bool(it.get("autopilot")) for it in items}
    for iid in released:
        try:
            if dev.set_work_item_status(dev_root, iid, "active"):
                dev_store.log_event(context_id, "item.resume",
                                    f"Upstream {upstream_id} {cause} — item released",
                                    item_id=iid, actor="daemon",
                                    meta={"upstream": str(upstream_id), "cause": cause})
                log.info("scheduler: released %s (upstream %s %s)", iid, upstream_id, cause)
                # An autopilot peer must not just go `active` and sit — it owes a triage run to
                # start its chain, the same first-kick the FE push and itemize give (slice 4c).
                # Hand-driven peers correctly wait for the owner's click.
                if autopilot.get(iid):
                    try:
                        from ..app_state import get_spine
                        from .runs import fire_auto_triage
                        fire_auto_triage(context_id, iid, get_spine())
                    except Exception:
                        log.exception("scheduler: auto-triage kick failed for %s", iid)
        except Exception:
            log.exception("scheduler: release failed for %s", iid)
    for iid in paged:
        try:
            if dev.set_work_item_status(dev_root, iid, "awaiting_human"):
                dev_store.log_event(context_id, "item.await",
                                    f"Upstream {upstream_id} ended without completing "
                                    f"({cause}) — this item needs a decision",
                                    item_id=iid, actor="daemon",
                                    meta={"upstream": str(upstream_id), "cause": cause})
                log.info("scheduler: paged %s (upstream %s %s)", iid, upstream_id, cause)
        except Exception:
            log.exception("scheduler: page failed for %s", iid)
    return {"released": released, "paged": paged}
