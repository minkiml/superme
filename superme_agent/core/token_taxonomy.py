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
    "triage": "workitem",
    "plan": "workitem",
    "build": "workitem",
    # A research item's own work: the investigation and the itemization of what it proposed. Its
    # review-entry write-up is no longer here — it is the shared `review` run (below). Same bucket
    # as the implementation phases: all the item's autonomous work.
    "investigate": "workitem",
    "itemize": "workitem",
    # The review-entry run, every kind (renovation §2.2). Before 2026-07-29 review had NO runner at
    # all, so this feature never appeared in a run row; research's ran under `research-report`.
    "review": "workitem",
    # Historical: the retired `report` PHASE and the absorbed `research-report` skill. Kept so runs
    # recorded before those changes still aggregate under work-item rather than self-flagging.
    "report": "workitem",
    "research-report": "workitem",
    # The loop's fresh-eyes verification runs (build-vet-loop §5) — same bucket as the build
    # cycles they gate.
    "vet": "workitem",
    "forge": "workitem",
    # Resolve-with-Agent (S4/D4): the background conflict-resolution run on an item's worktree —
    # an autonomous build op, same bucket as plan/build.
    "resolve": "workitem",
    # Interactive (owner-driven turns; bound chat still tags item_id for attribution).
    "chat": "interactive",
    # onboarding (project-init/retrofit walkthrough) and diagnosis (owner inspecting a run) are
    # deliberately bucketed under Other, not Interactive — they're meta/one-off work, not the main
    # interactive dev spend. Registered explicitly (vs left unclassified) so it's a decision, not a
    # "new unknown feature" self-flag; each still shows as its own named sub-bucket within Other.
    "onboarding": "other",
    "diagnosis": "other",
    # Throwaway prompt-extraction probe runs (prompt inspector): a disposable lifecycle minted only
    # to capture real per-phase input prompts, then torn down. Its tokens are real (kept trace) but
    # it's meta/one-off, not dev spend — bucketed under Other as its own named sub-bucket.
    "prompt-extraction": "other",
    # Kernel-driven context compaction (S8). Reports ZERO usage: the summarization happens inside
    # the CLI and never surfaces through the session, so there is nothing to count and the run row
    # stays 0. It is NOT free — it is unmeasured, and the shrink it achieved is a compaction metric
    # on the `compaction.verdict` event, not a usage figure (owner, 2026-07-28: do not mix column
    # definitions). Bucketed under Other as silent maintenance, never interactive spend.
    "compact": "other",
    # System / on-behalf features register here as they appear (e.g. "autotitle": "system").
}

# The catch-all: any feature not in the map lands here, KEEPING its own feature name as a sub-bucket.
UNCLASSIFIED = "other"


def category_for(feature: str | None) -> str:
    """Map a run feature to its category, defaulting unregistered features to the catch-all."""
    return FEATURE_CATEGORY.get(feature or "", UNCLASSIFIED)
