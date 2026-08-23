"""The system-level attention feed — every `awaiting_human` hold across every repo, classified by WHY
it is parked.

Nothing auto-stops forever: a hold notifies and offers an action, it never disposes.
"""

from __future__ import annotations

import logging

log = logging.getLogger("superme-agent")

# Hold kinds, which decide the quick actions the surface offers. The FE reads `kind`.
HOLD_KINDS = ("question", "escalation", "paged", "review", "gate")


def _ask_card(raw) -> list[dict]:
    """The grill's questions as the ask-card's four fields.

    A pre-typed report carried one prose string per question; surface those as `question` alone rather
    than dropping the hold."""
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


def classify_hold(item: dict, events: list[dict], *, rulings: list[dict] | None = None) -> dict:
    """Why is this `awaiting_human` item parked?

    Reads its events newest-first and takes the first that names a cause, falling back to the phase."""
    report_seen = False
    for e in events:
        kind = str(e.get("kind") or "")
        meta = e.get("meta") or {}
        summary = str(e.get("summary") or "")
        # The run ended on clarifying questions, so the owner unblocks it. Only the NEWEST report
        # may classify.
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
        if "page" in kind or kind.startswith("scheduler"):
            return {"kind": "paged", "reason": summary or "An upstream item ended without completing.",
                    "actor": "daemon"}
    phase = str(item.get("phase") or "")
    if phase == "review":
        # Say WHICH call the owner owes, on the card that summons them; otherwise the question is
        # invisible until they look.
        if rulings:
            n = len(rulings)
            return {"kind": "question",
                    "reason": f"Ready for your review — {n} proposal(s) wait on a call only you "
                              "can make. Approving without ruling drops them.",
                    "actor": "owner", "questions": rulings}
        # The deputy approved and the PR is open, so name the owner's narrower act, not the
        # generic gate.
        if item.get("git_pr_opened_at") and not item.get("git_merge_commit"):
            return {"kind": "review",
                    "reason": "Ready for your review and the PR is open",
                    "actor": "owner"}
        return {"kind": "review", "reason": "Ready for your review",
                "actor": "owner"}
    # The phase name opens the sentence, so it is capitalized here; the value is a lowercase enum.
    return {"kind": "gate",
            "reason": f"{(phase or 'current').capitalize()} is finished and waiting for your decision.",
            "actor": "owner"}


def _pending_rulings(dev_root, item: dict) -> list[dict]:
    """This item's proposals that ask the owner something and carry no answer.

    Only at REVIEW: before it the record does not exist, after it the item is terminal."""
    if str(item.get("phase")) != "review" or str(item.get("kind")) != "research":
        return []
    try:
        from pathlib import Path

        from ...core import artifacts as _arts
        _, held = _arts.filed_and_withheld(
            _arts.research_proposals(Path(dev_root) / "work-items" / str(item.get("id"))))
        return [{"question": p["question"], "recommend": p.get("suggested", ""),
                 "why": p.get("why_now", ""), "instead": p.get("title", "")}
                for p in held if p.get("question")]
    except Exception:
        log.exception("attention: failed to read pending rulings for %s", item.get("id"))
        return []


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
        c = classify_hold(it, events, rulings=_pending_rulings(dev_root, it))
        out.append({"id": it.get("id"), "title": it.get("title") or it.get("id"),
                    "session_id": it.get("session_id"),
                    "phase": it.get("phase"), "cohort": it.get("cohort"), **c})
    return out


def system_attention() -> list[dict]:
    """Fan out over every connected repo and collect its holds.

    Best-effort per repo: one that will not resolve is skipped, never failing the whole feed."""
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
