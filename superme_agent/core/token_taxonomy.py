"""Feature → category taxonomy for the token-usage breakdown.

An UNREGISTERED feature is never dropped: it surfaces under its own name inside `other`,
so it self-flags the first time it runs.
"""

# Stable top-level order for the dashboard (unknowns fall into the trailing catch-all).
CATEGORY_ORDER = ("learning", "workitem", "interactive", "system", "other")

CATEGORY_LABELS = {
    "learning": "Learning",
    "workitem": "Work-item",
    "interactive": "Interactive",
    "system": "System",
    "other": "Other",
}

# Extend with ONE line when a new agent ships.
FEATURE_CATEGORY = {
    # Learning agents (autonomous knowledge ops)
    "sweep": "learning",
    "capture": "learning",
    "distill": "learning",
    "write": "learning",
    "ratify": "learning",
    # Work-item agents (autonomous build ops)
    "triage": "workitem",
    "plan": "workitem",
    "build": "workitem",
    "close": "workitem",
    # Runs per item at that item's gates, so its spend belongs with the phases it judges.
    "deputy": "workitem",
    # A research item's own work — same bucket as the implementation phases.
    "investigate": "workitem",
    "itemize": "workitem",
    # The review-entry run, every kind.
    "review": "workitem",
    # Historical, and folded into `review` for display — see FEATURE_ALIAS.
    "report": "workitem",
    "research-report": "workitem",
    # The loop's fresh-eyes verification runs, same bucket as the builds they gate.
    "vet": "workitem",
    "forge": "workitem",
    # Background conflict resolution on an item's worktree — an autonomous build op.
    "resolve": "workitem",
    # Interactive (owner-driven turns; bound chat still tags item_id for attribution).
    "chat": "interactive",
    # Meta and one-off, not the main interactive spend.
    "onboarding": "other",
    "diagnosis": "other",
    # A throwaway probe: real tokens, but meta rather than dev spend.
    "prompt-extraction": "other",
    # Reports ZERO usage: the CLI summarizes internally. Not free — UNMEASURED.
    "compact": "other",
    # System / on-behalf features register here as they appear (e.g. "autotitle": "system").
}

# Anything unmapped lands here, KEEPING its own feature name as a sub-bucket.
UNCLASSIFIED = "other"

# Retired feature → the one that absorbed it. Presentation only: rows keep their spelling, amounts
# untouched.
FEATURE_ALIAS = {
    # Both were absorbed into the one shared `review` run.
    "report": "review",
    "research-report": "review",
}

# Shown as a SINGLE bar — a breakdown only earns its width where the parts are separately
# actionable.
COLLAPSED_CATEGORIES = ("learning", "other")


def category_for(feature: str | None) -> str:
    """Map a run feature to its category, defaulting unregistered features to the catch-all."""
    return FEATURE_CATEGORY.get(display_feature(feature), UNCLASSIFIED)


def display_feature(feature: str | None) -> str:
    """The name this feature's work is reported under today — itself, unless it was renamed."""
    f = feature or ""
    return FEATURE_ALIAS.get(f, f)
