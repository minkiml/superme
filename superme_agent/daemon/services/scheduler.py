"""Peer scheduler — the `after:` edge's release hook.

One function rather than four inlined blocks, because an item can go terminal down four paths, and
a peer only some of them release is work that silently never runs.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...core.vocab import status_router

log = logging.getLogger("superme-agent")


def release_downstream(dev, dev_root: Path, dev_store, context_id: str,
                       items: list[dict], upstream_id: str, *, cause: str) -> dict:
    """Fire the peers waiting on `upstream_id`.

    Released: every upstream completed. Paged: one ended abandoned, so nothing will release it and
    auto-starting would build on a predecessor that never landed."""
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
                # An autopilot peer owes a triage run to start its chain; a hand-driven one
                # correctly waits for the owner.
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
