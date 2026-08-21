"""Autopilot — the per-item policy that turns a gate from a click into an auto-transition.

Pure decision logic over item dicts, like `status_router`. The daemon owns the firing.

    Autopilot never JUDGES; it only removes waiting.
    Review is an exclusion zone — autopilot goes dormant there and resumes on exit.

So this encodes mechanical eligibility only. WHO supplies the judgment before an advance fires
is the caller's concern: a returned target does not authorize an unjudged advance.
"""

# The one gate autopilot never drives itself through. `close` needs no entry: it has no next
# phase, so it falls out naturally.
REVIEW_PHASE = "review"

# Stamped on every throwaway probe run — marks the kept trace and buckets its token spend.
PROMPT_EXTRACTION_FEATURE = "prompt-extraction"

# The phases that occupy a compute slot. triage/plan are cheap and uncapped.
BUILD_SLOT_PHASES = ("build", "vet")
AWAITING_SLOT = "awaiting_slot"


def _terminal(item: dict) -> bool:
    return str(item.get("status")) == "done" or bool(item.get("done_at"))


def occupied_build_slots(items: list[dict]) -> int:
    """How many AUTOPILOT items sit in the build⟷vet loop. Hand-driven items are the owner's
    explicit choice and are never counted or held."""
    return sum(1 for it in items
               if is_autopilot(it) and not _terminal(it)
               and str(it.get("phase")) in BUILD_SLOT_PHASES)


def free_build_slots(items: list[dict], cap: int) -> int:
    """Open autopilot build⟷vet slots = max(0, cap − occupied)."""
    return max(0, int(cap) - occupied_build_slots(items))


def held_for_slot(items: list[dict]) -> list[dict]:
    """Autopilot items parked at `awaiting_slot`, oldest first — the set the pump releases when
    a slot frees. FIFO by `updated_at`."""
    parked = [it for it in items if str(it.get("status")) == AWAITING_SLOT and is_autopilot(it)]
    return sorted(parked, key=lambda it: str(it.get("updated_at") or ""))


def is_autopilot(item: dict) -> bool:
    """The per-item policy flag, absent → False (the hand-driven default)."""
    return bool(item.get("autopilot"))


def is_prompt_extraction(item: dict) -> bool:
    """The throwaway prompt-extraction flag: a disposable item minted to run a real lifecycle
    so its per-phase prompts can be captured, then torn down. Absent → False."""
    return bool(item.get("prompt_extraction"))


def auto_advance_target(item: dict, next_phase) -> str | None:
    """The phase autopilot would advance this item into, or None if it must NOT.

    None when autopilot is off, the item is terminal or not resting at a gate, it is at REVIEW
    (the exclusion zone), or there is no next phase. A returned phase means mechanically
    advanceable — not that the advance is safe."""
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


def throwaway_advance_target(item: dict, next_phase) -> str | None:
    """Like `auto_advance_target` but for a throwaway probe: it advances through EVERY gate,
    review included, because a throwaway needs no judgment and its merge is synthetic-skipped."""
    if not is_prompt_extraction(item):
        return None
    if str(item.get("status")) != "awaiting_human" or item.get("done_at"):
        return None
    try:
        nxt = next_phase(item.get("kind"), str(item.get("phase") or ""))
    except KeyError:
        return None
    return nxt or None
