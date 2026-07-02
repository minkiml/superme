"""Feature → category taxonomy for token-usage Breakdown 1 (the semantic axis).

ONE governed place mapping each run `feature` to a top-level category. A new agent emits a new
`feature` (an open TEXT column on `run` — no schema change needed); classify it here with a single
line. An UNREGISTERED feature is never silently dropped: it surfaces under its own name inside the
catch-all `other` category (self-flagging — it shows the first time it runs, prompting classification).
This keeps the "no silent usage" guarantee on the semantic axis, not just the token-type axis.

See general_docs/token-usage-tracking-spec.md (decision 5).
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

# run.feature → category. Extend with ONE line when a new agent ships. `chat` is deliberately not
# pre-split into user/system sub-features — there is one interactive origin today; genuinely
# system-initiated turns will earn their own feature (mapped to "system") when they exist.
FEATURE_CATEGORY = {
    # Learning agents (autonomous knowledge ops)
    "sweep": "learning",
    "capture": "learning",
    "distill": "learning",
    "write": "learning",
    "ratify": "learning",
    # Work-item agents (autonomous build ops)
    "plan": "workitem",
    "build": "workitem",
    "forge": "workitem",
    # Interactive (owner-driven turns; bound chat still tags item_id for attribution)
    "chat": "interactive",
    # System / on-behalf features register here as they appear (e.g. "autotitle": "system").
}

# The catch-all: any feature not in the map lands here, KEEPING its own feature name as a sub-bucket.
UNCLASSIFIED = "other"


def category_for(feature: str | None) -> str:
    """Map a run feature to its category, defaulting unregistered features to the catch-all."""
    return FEATURE_CATEGORY.get(feature or "", UNCLASSIFIED)
