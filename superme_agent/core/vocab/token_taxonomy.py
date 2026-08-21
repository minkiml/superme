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
    "deputy": "workitem",
    "investigate": "workitem",
    "itemize": "workitem",
    "review": "workitem",
    # Historical; folded into `review` for display — see FEATURE_ALIAS.
    "report": "workitem",
    "research-report": "workitem",
    "vet": "workitem",
    "forge": "workitem",
    "resolve": "workitem",
    "chat": "interactive",
    # Meta and one-off, not the main interactive spend.
    "onboarding": "other",
    "diagnosis": "other",
    "prompt-extraction": "other",
    # Reports ZERO usage: the CLI summarizes internally. Not free — UNMEASURED.
    "compact": "other",
}

# Anything unmapped lands here, KEEPING its own feature name as a sub-bucket.
UNCLASSIFIED = "other"

# Retired feature → the one that absorbed it. Presentation only: rows keep their spelling, amounts
# untouched.
FEATURE_ALIAS = {
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
