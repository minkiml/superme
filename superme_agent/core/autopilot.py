"""Autopilot — the per-item policy that turns a gate from a click into an auto-transition.

Mechanical eligibility only; it never JUDGES. Review is an exclusion zone. The daemon fires.
"""

# The one gate autopilot never drives itself through.
REVIEW_PHASE = "review"

# Stamped on every throwaway probe run — marks the kept trace and buckets its token spend.
PROMPT_EXTRACTION_FEATURE = "prompt-extraction"

# The phases that occupy a compute slot. triage/plan are cheap and uncapped.
BUILD_SLOT_PHASES = ("build", "vet")
AWAITING_SLOT = "awaiting_slot"


def _terminal(item: dict) -> bool:
    return str(item.get("status")) == "done" or bool(item.get("done_at"))


def occupied_build_slots(items: list[dict]) -> int:
    """How many AUTOPILOT items sit in the build⟷vet loop. Hand-driven items are never counted or held."""
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
    """A disposable item minted to run a real lifecycle so its prompts get captured, then torn down."""
    return bool(item.get("prompt_extraction"))


def auto_advance_target(item: dict, next_phase) -> str | None:
    """The phase autopilot would advance into, or None. Mechanically advanceable does not mean safe."""
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
    """Like `auto_advance_target`, but a throwaway clears EVERY gate, review included. Nothing to judge."""
    if not is_prompt_extraction(item):
        return None
    if str(item.get("status")) != "awaiting_human" or item.get("done_at"):
        return None
    try:
        nxt = next_phase(item.get("kind"), str(item.get("phase") or ""))
    except KeyError:
        return None
    return nxt or None
