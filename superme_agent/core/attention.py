"""Attention engine — "what needs me?" answered mechanically (workspace-workflow S7, D10;
nimbalyst near-verbatim).

Every work-item lands in AT MOST one bucket, strict priority:
    error    (red)         — the work STOPPED: a crashed turn, an outage that outlasted the retry
                             ladder, a daemon restart (durable `status == error`, carrying the
                             `error_reason` it stopped with). Ranked above `needs_you` because an
                             item resting at a gate is the workflow working as designed, while this
                             one is not working at all. It is never terminal — the owner Resumes it
                             or re-runs it — and it is NOT the run-level `system_fault`, which is a
                             run that finished while our machinery misbehaved (recovery R2).
    needs_you (orange)     — sits at a human gate: durable `status == awaiting_human`, never an
                             ephemeral flag (restart-proof; only awaiting{human} pages — child/
                             external stay silent, D2). Reserved for a TRUE human decision.
                             ALSO covers the STALL: `active` at a gate phase with no live run.
                             "Active" means being worked, and at a gate with nothing running
                             there is nothing working and nothing that will start — only the
                             owner moves it. Leaving those quiet is the one failure this engine
                             exists to prevent, so they page (see `_reason`, which says which
                             of the two it is).
    deputy_working (purple)— a live run whose actor is the DEPUTY (the owner's delegated reviewer)
                             is judging a gate right now. Split out of `running` so the board never
                             reads "NEEDS YOU" (or a generic "agent working") while the deputy is
                             covering the owner — the deputy is standing in for the human here.
    running  (green)       — a live PHASE-agent run is executing for it right now (spine live rows).
    unread   (blue)        — terminal, and the owner hasn't opened it since (no `seen_at` stamp):
                             the close/abandon brief pushing back instead of waiting to be polled.
Everything else is quiet — active-but-autonomous work makes no attention claim.

The global badge shows ONLY the top non-empty tier (its color + count) — one glance, one number.
Derived at read time from durable state; nothing stored, nothing to drift.
"""

from .gate_briefs import GATE_FOR_PHASE
from .kind_profiles import get_profile

TIER_ORDER = ("error", "needs_you", "deputy_working", "running", "unread")
TIER_COLOR = {"error": "red", "needs_you": "orange", "deputy_working": "purple",
              "running": "green", "unread": "blue"}


def _is_terminal(item: dict) -> bool:
    return bool(item.get("done_at")) or str(item.get("status")) == "done"


def _reason(item: dict, bucket: str, stalled: bool = False) -> str:
    phase = str(item.get("phase") or "")
    if bucket == "error":
        # The stored reason IS the message — written where the work stopped, by the runner that
        # watched it stop. This reader never re-derives it: guessing a cause from the phase alone
        # is how "unexpected error" labels that tell the owner nothing get born.
        why = str(item.get("error_reason") or "").strip()
        where = f"during {phase}" if phase else "mid-run"
        return f"the work stopped {where} — {why}" if why else f"the work stopped {where}"
    if bucket == "needs_you":
        gate = GATE_FOR_PHASE.get(phase)
        if stalled:
            # Worth distinguishing: a normal gate pause is the workflow working as designed, while
            # this one reached a gate and lost its run. Same claim on the owner, different story.
            return f"stalled at the {gate} gate — active with nothing running" if gate \
                else f"stalled (mid-{phase}) — active with nothing running"
        return f"at the {gate} gate — your decision" if gate \
            else f"awaiting you (mid-{phase})"
    if bucket == "deputy_working":
        gate = GATE_FOR_PHASE.get(phase)
        return f"deputy reviewing the {gate} gate" if gate else f"deputy reviewing ({phase})"
    if bucket == "running":
        return f"agent working ({phase})"
    return f"{item.get('outcome') or 'closed'} — unreviewed"


def assign(items: list[dict], running_ids: set[str], deputy_ids: set[str] = frozenset()) -> dict:
    """Bucket every item → {buckets: {tier: [row…]}, badge: {tier, color, count} | None}.
    A row carries what the kanban/badge surfaces need: id · title · kind · phase · status ·
    outcome · bucket · reason (one human line) · gate (when parked at one).

    `deputy_ids` ⊆ `running_ids` — the subset whose live run is a deputy judgment (stamped
    `feature="deputy"`). They peel out of the green `running` tier into `deputy_working` so the
    owner can tell "the deputy is covering this" from "a phase agent is coding"."""
    buckets: dict[str, list[dict]] = {t: [] for t in TIER_ORDER}
    for it in items:
        iid = str(it.get("id"))
        terminal = _is_terminal(it)
        stalled = False
        # FIRST, above every other claim: a stopped item is not resting, it is broken, and it stays
        # invisible to every tier below (they all key on active/awaiting_human, which it is not).
        if str(it.get("status")) == "error" and not terminal:
            tier = "error"
        elif str(it.get("status")) == "awaiting_human":
            tier = "needs_you"
        elif iid in deputy_ids and not terminal:
            tier = "deputy_working"
        elif iid in running_ids and not terminal:
            tier = "running"
        # AFTER the two live tiers on purpose: an item at a gate WITH a run is being worked, and
        # only its absence makes the gate a stall. Deputy ⊆ running, so both are already excluded.
        # A freshly-advanced item blips here for the moment between the advance and its run row
        # appearing; it heals itself on the next read, and a brief page beats a silent wedge.
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
            "reason": _reason(it, tier, stalled),
            "gate": GATE_FOR_PHASE.get(str(it.get("phase") or "")) if tier == "needs_you" else None,
        })
    badge = None
    for tier in TIER_ORDER:
        if buckets[tier]:
            badge = {"tier": tier, "color": TIER_COLOR[tier], "count": len(buckets[tier])}
            break
    return {"buckets": buckets, "badge": badge}
