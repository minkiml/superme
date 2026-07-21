"""Launch a cohort — the onboarding itemize step's daemon side (autopilot slice 4c).

`core/dev_knowledge.itemize_launch` does the CREATION (topological, cohort-stamped, edge-resolved,
loop-free so it unit-tests without a daemon); this module fires the first triage run for each item
that starts `active`, so the autopilot chain actually begins. Items parked `awaiting_upstream` are
kicked later by the scheduler's peer release (`services.scheduler.release_downstream`), which runs
the same `fire_auto_triage` first-kick when an upstream completes.

`cohort_spend` is the OBSERVABILITY read (no breaker): the aggregate build+vet token spend across a
launch cohort. The launch budget was deliberately NOT built as a hard ceiling — the per-item budget
already contains each item with hold-and-page semantics; this exposes the aggregate so it's visible.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..app_state import dev as _dev, dev_store as _dev_store, spine as _spine
from .runs import fire_auto_triage

log = logging.getLogger("superme-agent")


def launch_cohort(context_id: str, items: list[dict], *, actor: str = "agent") -> dict:
    """Create the cohort (via `itemize_launch`), log the launch, and fire the first triage run for
    every item that starts `active`. Returns the itemize result augmented with the close-out shape
    the skill renders: `{cohort, created, running, waiting, launched}` where `launched` is the count
    of items whose triage actually started this call (the rest wait on upstreams). Raises
    RuntimeError if the context has no internal (dev) root, ValueError on a bad batch (bubbled up
    from itemize_launch — cyclic/unknown edge, missing title)."""
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
