"""Launch a cohort — the onboarding itemize step's daemon side.

`itemize_launch` does the creation; this fires the first triage run for each item that starts
`active`. `cohort_spend` is an observability read, not a breaker.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..app_state import dev as _dev, dev_store as _dev_store, spine as _spine
from .runs import fire_auto_triage

log = logging.getLogger("superme-agent")


def launch_cohort(context_id: str, items: list[dict], *, actor: str = "agent") -> dict:
    """Create the cohort, log the launch, and fire the first triage run for every item that starts
    `active`.

    `launched` counts the items whose triage actually started this call; the rest wait on upstreams."""
    from ...gateway import contexts
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise RuntimeError(f"context {context_id!r} has no dev root — cannot launch a cohort")
    dev_root = ctx.internal_root / "dev"
    result = _dev.itemize_launch(dev_root, items)
    cohort = result["cohort"]
    _dev_store.log_event(
        context_id, "cohort.launch",
        f"Launched {len(result['created'])} item(s) on autopilot "
        f"({len(result['running'])} running now, {len(result['waiting'])} waiting on upstreams)",
        actor=actor,
        meta={"cohort": cohort, "items": [c["id"] for c in result["created"]],
              "running": result["running"], "waiting": result["waiting"]},
    )
    launched = 0
    for c in result["created"]:
        if c["status"] == "active" and fire_auto_triage(context_id, c["id"], _spine):
            launched += 1
    result["launched"] = launched
    log.info("launch: cohort %s — %d items, %d triage runs fired", cohort,
             len(result["created"]), launched)
    return result


def cohort_spend(context_id: str, dev_root: Path, cohort: str) -> dict:
    """Aggregate build+vet token spend across one launch cohort — observability, not a breaker.
    Sums each cohort item's `item_phase_tokens` (3-type: input + cache-write + output, EXCLUDING
    cache-read; see spine._display_tokens). Returns `{cohort, items, spent}`."""
    members = [it for it in _dev.read_all(dev_root)["work_items"]
               if it.get("cohort") == cohort]
    spent = sum(_spine.item_phase_tokens(context_id, str(it["id"])) for it in members)
    return {"cohort": cohort, "items": len(members), "spent": spent}
