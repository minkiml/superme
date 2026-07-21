"""The system-level attention feed (Pass 2 · Q2) — every `awaiting_human` hold across EVERY
connected repo, each classified by WHY it's parked so the top-of-SuperMe notification center can
show it with the right quick actions. Owner principle: nothing auto-stops forever — a hold notifies
and offers Proceed / go-to / Have-it-fixed, it never disposes.

`classify_hold` is PURE (item dict + its recent events → kind/reason/actor), so it's unit-testable
without a daemon; the IO wrappers just read the ledgers and fan out over repos.
"""

from __future__ import annotations

import logging

log = logging.getLogger("superme-agent")

# Hold kinds → what quick actions the surface should offer (the FE reads `kind`):
#   escalation — the deputy paged the owner with a runbook   → Proceed · go-to · Have it fixed
#   breaker    — a build⟷vet breaker stopped the loop (WIP)  → Proceed (grant/continue) · go-to
#   paged      — an upstream ended without completing         → go-to (decide the dependent's fate)
#   review     — the normal review gate (vet green, waiting)  → go-to (Approve & merge)
#   gate       — any other gate decision the owner owes       → go-to
HOLD_KINDS = ("escalation", "breaker", "paged", "review", "gate")


def classify_hold(item: dict, events: list[dict]) -> dict:
    """Why is this awaiting_human item parked? Read its events NEWEST-FIRST and take the first that
    names a parking cause; fall back to the phase (review = the review gate, else a generic gate
    wait). Returns {kind, reason, actor} — pure, no IO."""
    for e in events:
        kind = str(e.get("kind") or "")
        meta = e.get("meta") or {}
        summary = str(e.get("summary") or "")
        if kind.startswith("deputy.escalate"):
            return {"kind": "escalation", "reason": summary or "The deputy escalated this gate to you.",
                    "actor": "deputy"}
        if kind == "loop.decision" and str(meta.get("action")) == "halt":
            return {"kind": "breaker", "reason": summary or "The build⟷vet loop stopped with WIP preserved.",
                    "actor": "daemon"}
        if "page" in kind or kind.startswith("scheduler"):
            return {"kind": "paged", "reason": summary or "An upstream item ended without completing.",
                    "actor": "daemon"}
    phase = str(item.get("phase") or "")
    if phase == "review":
        return {"kind": "review", "reason": "Ready for your review — Approve & merge, or send back.",
                "actor": "owner"}
    return {"kind": "gate", "reason": f"Waiting for your decision at the {phase or 'current'} gate.",
            "actor": "owner"}


def holds_for_repo(context_id: str, *, dev, dev_store, dev_root) -> list[dict]:
    """Every parked (`awaiting_human`, non-terminal) work-item in one repo, classified. Best-effort
    per item — a bad event read never drops the item, it just gets the generic gate reason."""
    try:
        items = dev.read_all(dev_root)["work_items"]
    except Exception:
        log.exception("attention: failed to read work-items for %s", context_id)
        return []
    out: list[dict] = []
    for it in items:
        if it.get("done_at") or str(it.get("status")) != "awaiting_human":
            continue
        try:
            events = dev_store.list_events(context_id, item_id=str(it.get("id")), limit=25)
        except Exception:
            events = []
        c = classify_hold(it, events)
        out.append({"id": it.get("id"), "title": it.get("title") or it.get("id"),
                    "session_id": it.get("session_id"),
                    "phase": it.get("phase"), "cohort": it.get("cohort"), **c})
    return out


def system_attention() -> list[dict]:
    """Fan out over EVERY connected repo (dev scope — core has no work-items) and collect its holds.
    Returns [{repo_id, repo_label, holds:[…]}] for repos that have at least one hold, so the top-of-
    SuperMe center shows only what needs the owner. Best-effort per repo (a repo that won't resolve is
    skipped, never fails the whole feed)."""
    from .. import app_state
    from ..app_state import get_spine
    from ...gateway import contexts
    spine = get_spine()
    dev = app_state.dev
    dev_store = app_state.dev_store
    feed: list[dict] = []
    for rc in spine.repos().values():
        try:
            ctx = contexts.resolve(rc.id, "dev")
            if not ctx.internal_root:
                continue
            holds = holds_for_repo(rc.id, dev=dev, dev_store=dev_store,
                                   dev_root=ctx.internal_root / "dev")
        except Exception:
            log.exception("attention: repo %s failed", rc.id)
            continue
        if holds:
            feed.append({"repo_id": rc.id, "repo_label": getattr(rc, "label", None) or rc.id,
                         "holds": holds})
    return feed
