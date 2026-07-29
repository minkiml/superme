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
#   question   — the plan agent paused on clarifying questions → open chat (answer them)
#   escalation — the deputy paged the owner with a runbook   → Proceed · go-to · Have it fixed
#   breaker    — a build⟷vet breaker stopped the loop (WIP)  → Proceed (grant/continue) · go-to
#   paged      — an upstream ended without completing         → go-to (decide the dependent's fate)
#   review     — the normal review gate (vet green, waiting)  → go-to (Approve & merge)
#   gate       — any other gate decision the owner owes       → go-to
HOLD_KINDS = ("question", "escalation", "breaker", "paged", "review", "gate")


def _ask_card(raw) -> list[dict]:
    """The grill's questions as the ask-card's four fields. Pre-typed reports (and any hand-written
    event) carried one prose string per question — surface those as `question` alone rather than
    dropping the hold; the fields are enforced at the tool, not re-litigated here."""
    out: list[dict] = []
    for q in raw or []:
        if isinstance(q, dict):
            fields = {k: str(q.get(k) or "").strip() for k in
                      ("question", "recommend", "why", "instead")}
            if fields["question"]:
                out.append({k: v for k, v in fields.items() if v})
        elif str(q).strip():
            out.append({"question": str(q).strip()})
    return out


def classify_hold(item: dict, events: list[dict]) -> dict:
    """Why is this awaiting_human item parked? Read its events NEWEST-FIRST and take the first that
    names a parking cause; fall back to the phase (review = the review gate, else a generic gate
    wait). Returns {kind, reason, actor} — pure, no IO."""
    report_seen = False
    for e in events:
        kind = str(e.get("kind") or "")
        meta = e.get("meta") or {}
        summary = str(e.get("summary") or "")
        # Plan's grill (renovation §2): the run ended on clarifying questions — the owner is the
        # unblocking actor, and the questions themselves ride the hold for the ask-card. Only the
        # NEWEST run.report may classify: a later run's outcome supersedes an older needs_user.
        if kind == "run.report":
            if not report_seen and str(meta.get("outcome")) == "needs_user":
                qs = _ask_card((meta.get("user") or {}).get("questions"))
                return {"kind": "question",
                        "reason": summary or "The agent has questions before it can finish the plan.",
                        "actor": "agent", "questions": qs}
            report_seen = True
            continue
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
        # `strict` repos (§2.2): the deputy already approved and the PR is open, so the owner's
        # act is narrower and lives elsewhere — say which one it is rather than offering the
        # generic gate. Derived from the item's own record; still pure.
        if item.get("git_pr_opened_at") and not item.get("git_merge_commit"):
            return {"kind": "review",
                    "reason": "The PR is open and the merge is yours — read the diff on the PR "
                              "page, then approve.",
                    "actor": "owner"}
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
