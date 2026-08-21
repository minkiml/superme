"""Attention engine — "what needs me?", answered mechanically.

Every item lands in AT MOST one bucket, strict priority: `error` (work stopped) ·
`needs_you` (a human gate, or stalled at one) · `deputy_working` · `running` · `resting`.
"""

from .gate_briefs import GATE_FOR_PHASE
from .kind_profiles import get_profile

TIER_ORDER = ("error", "needs_you", "deputy_working", "running", "unread")
TIER_COLOR = {"error": "red", "needs_you": "orange", "deputy_working": "purple",
              "running": "green", "unread": "blue"}


def _is_terminal(item: dict) -> bool:
    return bool(item.get("done_at")) or str(item.get("status")) == "done"


def _reason(item: dict, bucket: str, stalled: bool = False, rulings: int = 0) -> str:
    phase = str(item.get("phase") or "")
    if bucket == "error":
        # The stored reason IS the message. Re-deriving it from the phase is how "unexpected
        # error" is born.
        why = str(item.get("error_reason") or "").strip()
        where = f"during {phase}" if phase else "mid-run"
        return f"the work stopped {where} — {why}" if why else f"the work stopped {where}"
    if bucket == "needs_you":
        gate = GATE_FOR_PHASE.get(phase)
        if stalled:
            # A normal gate pause is the workflow working; this one reached a gate and lost its run.
            return f"stalled at the {gate} gate — active with nothing running" if gate \
                else f"stalled (mid-{phase}) — active with nothing running"
        # An item that ASKS must not read like one that merely finished.
        if rulings:
            return (f"at the {gate} gate — {rulings} proposal(s) need a call only you can make "
                    "(approving without ruling drops them)")
        return f"at the {gate} gate — your decision" if gate \
            else f"awaiting you (mid-{phase})"
    if bucket == "deputy_working":
        gate = GATE_FOR_PHASE.get(phase)
        return f"deputy reviewing the {gate} gate" if gate else f"deputy reviewing ({phase})"
    if bucket == "running":
        return f"agent working ({phase})"
    return f"{item.get('outcome') or 'closed'} — unreviewed"


def assign(items: list[dict], running_ids: set[str], deputy_ids: set[str] = frozenset(),
           rulings_by_item: dict[str, int] | None = None) -> dict:
    """Bucket every item → {buckets, badge}. `deputy_ids` ⊆ `running_ids`: the subset judging a
    gate, as opposed to coding."""
    buckets: dict[str, list[dict]] = {t: [] for t in TIER_ORDER}
    for it in items:
        iid = str(it.get("id"))
        terminal = _is_terminal(it)
        stalled = False
        # FIRST: a stopped item is broken, and every tier below keys on states it is not in.
        if str(it.get("status")) == "error" and not terminal:
            tier = "error"
        elif str(it.get("status")) == "awaiting_human":
            tier = "needs_you"
        elif iid in deputy_ids and not terminal:
            tier = "deputy_working"
        elif iid in running_ids and not terminal:
            tier = "running"
        # Only the ABSENCE of a run makes a gate a stall. A just-advanced item blips, then heals.
        elif (str(it.get("status")) == "active" and not terminal
              and str(it.get("phase") or "") in GATE_FOR_PHASE):
            tier, stalled = "needs_you", True
        elif terminal and not it.get("seen_at"):
            tier = "unread"
        else:
            continue
        buckets[tier].append({
            "id": iid, "title": it.get("title") or iid,
            "kind": get_profile(it.get("kind")).kind,
            "phase": it.get("phase"), "status": it.get("status"),
            "outcome": it.get("outcome"), "bucket": tier,
            "reason": _reason(it, tier, stalled, (rulings_by_item or {}).get(iid, 0)),
            "gate": GATE_FOR_PHASE.get(str(it.get("phase") or "")) if tier == "needs_you" else None,
        })
    badge = None
    for tier in TIER_ORDER:
        if buckets[tier]:
            badge = {"tier": tier, "color": TIER_COLOR[tier], "count": len(buckets[tier])}
            break
    return {"buckets": buckets, "badge": badge}
