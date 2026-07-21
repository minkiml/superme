"""Autopilot — the per-item policy that turns a gate from a click into an auto-transition.

Pure decision logic over plain item dicts (no IO, no spine), like `status_router`. The daemon
owns the firing; this module only answers *should this gate advance itself, and to where*.

The governing invariant (autopilot-launch-design §2b, owner 2026-07-20):

    Autopilot never JUDGES; it only removes waiting.
    Review is an exclusion zone — autopilot goes dormant there and resumes on exit.
    Only a human or a deputy exits review.

So this module deliberately encodes only the *mechanical* eligibility (is this item on autopilot,
is it resting at an advanceable gate). WHO supplies the judgment before the advance fires — a deputy
(slice 4) or, in its absence, the owner — is the caller's concern, not decided here. An autopilot
item with no deputy configured must fall back to the owner at every gate; this function returning a
target does not by itself authorize an unjudged advance.
"""

# The one gate autopilot never drives itself through — review is where the owner (or their deputy)
# runs the thing. `close` isn't listed because it has no next phase anyway (it's terminal-adjacent),
# so `next_phase` returns None and it falls out naturally.
REVIEW_PHASE = "review"

# The phases that occupy a build⟷vet compute slot — the expensive, long-running loop the
# concurrency cap governs. triage/plan are cheap and uncapped; review/close are past the loop.
BUILD_SLOT_PHASES = ("build", "vet")
AWAITING_SLOT = "awaiting_slot"


def _terminal(item: dict) -> bool:
    return str(item.get("status")) == "done" or bool(item.get("done_at"))


def occupied_build_slots(items: list[dict]) -> int:
    """How many AUTOPILOT items currently sit in the build⟷vet loop (non-terminal, phase in
    build/vet). The cap governs autonomous concurrency only — hand-driven items in build are the
    owner's explicit choice and are not counted or held (autopilot must not throttle a human)."""
    return sum(1 for it in items
               if is_autopilot(it) and not _terminal(it)
               and str(it.get("phase")) in BUILD_SLOT_PHASES)


def free_build_slots(items: list[dict], cap: int) -> int:
    """Open autopilot build⟷vet slots = max(0, cap − occupied)."""
    return max(0, int(cap) - occupied_build_slots(items))


def held_for_slot(items: list[dict]) -> list[dict]:
    """Autopilot items parked at `awaiting_slot` (the concurrency queue), oldest first — the set the
    pump releases when a slot frees. Order by `updated_at` so the earliest-queued goes first (FIFO;
    absent sorts first, which is fine — it just means 'been waiting since before the stamp')."""
    parked = [it for it in items if str(it.get("status")) == AWAITING_SLOT and is_autopilot(it)]
    return sorted(parked, key=lambda it: str(it.get("updated_at") or ""))


def is_autopilot(item: dict) -> bool:
    """The per-item policy flag, absent → False (the hand-driven default)."""
    return bool(item.get("autopilot"))


def auto_advance_target(item: dict, next_phase) -> str | None:
    """The phase autopilot would advance this item into, or None if it must NOT auto-advance.

    `next_phase` is `kind_profiles.next_phase` (passed in to keep this module dependency-free and
    unit-testable). Returns None — leave it for a human/deputy — when:

    - autopilot is off (the item was never enrolled)
    - the item is terminal, or not resting at a gate (`awaiting_human` is the only gate-rest state;
      `awaiting_upstream` is a peer wait the scheduler owns, `active` means a run is mid-flight)
    - the item is at REVIEW — the exclusion zone
    - there is no next phase (it's at close)

    A returned phase means "mechanically advanceable". It is NOT a judgment that the advance is
    safe — the caller must still route it through a deputy or the owner.
    """
    if not is_autopilot(item):
        return None
    if str(item.get("status")) != "awaiting_human" or item.get("done_at"):
        return None
    phase = str(item.get("phase") or "")
    if phase == REVIEW_PHASE:
        return None
    try:
        nxt = next_phase(item.get("kind"), phase)
    except KeyError:
        return None
    if not nxt:
        return None
    return nxt
