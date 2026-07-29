"""KIND_PROFILES — the one in-code table mapping a work-item KIND to its machinery (workspace-
workflow D1/D2). A kind exists only when it changes MACHINERY (phases, git isolation, knowledge
writes, artifacts, close criteria) — content differences (feature/bug/refactor/docs) are all
`implementation`. Extending = adding a profile entry; an unknown kind fails LOUD (never a silent
default), so a typo can't run an item through the wrong machinery.

Item-kind is orthogonal to session-kind (core/kernel_speech.py): they join at
`(item.kind, item.phase)` → the phase-session behavior contract (S5).
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KindProfile:
    kind: str
    # Ordered phase pipeline. The FIRST phase is the intake (triage); the LAST is `close`.
    phases: tuple[str, ...]
    # Does this kind get git isolation (branch + worktree, S4)? Research reads main, changes nothing.
    worktree: bool
    # May this kind write general dev-knowledge (anchor docs) at merge (D7)? Research: never.
    knowledge_writes: bool
    # phase → artifact kinds that phase EMITS (D6; consumed by the next gate). Scaffolding lands S2.
    emits: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Artifact kinds the close gate mechanically REQUIRES to exist (D6 §8; enforced S6).
    required_artifacts: tuple[str, ...] = ()
    # Close-criteria keys the close gate checks (D8; evaluated S6). Declarative slugs, not code.
    close_criteria: tuple[str, ...] = ()


KIND_PROFILES: dict[str, KindProfile] = {
    "implementation": KindProfile(
        kind="implementation",
        phases=("triage", "plan", "build", "vet", "review", "close"),
        worktree=True,
        knowledge_writes=True,
        emits={
            "triage": ("brief",),
            "plan": ("plan",),
        },
        required_artifacts=("plan",),
        # No merge check here: review's EXIT is the merge (`advance_item` runs `review_merge` and
        # 409s on conflict), so an item cannot reach close unmerged. A criterion that can only fail
        # for a reason close has no move to fix is theatre — the merge is review's to get right,
        # and review is the last gate where the owner can still act on it.
        close_criteria=(
            "evidence_fresh", "knowledge_row_resolved", "children_terminal",
        ),
    ),
    # The spine (triage · plan · review · close) is shared by every kind; only ‹WORK› differs —
    # `investigate` here, `build ⟷ vet` above. The old `report` PHASE is retired: it sat where
    # `review` belongs, and its work is now the shared review-ENTRY run — ONE `review` skill for
    # every kind, with a per-kind report template inside it (renovation §2.2).
    "research": KindProfile(
        kind="research",
        phases=("triage", "plan", "investigate", "review", "close"),
        worktree=False,
        knowledge_writes=False,
        emits={
            "triage": ("brief",),
            "plan": ("plan",),
            "investigate": ("investigation",),
        },
        required_artifacts=("plan", "investigation"),
        close_criteria=("findings_delivered", "spawns_exist"),
    ),
}

DEFAULT_KIND = "implementation"

# Every phase any kind can be in (schema Literal mirrors this — keep in sync with
# daemon/schemas/common.py WorkPhase).
ALL_PHASES: tuple[str, ...] = tuple(dict.fromkeys(
    p for prof in KIND_PROFILES.values() for p in prof.phases
))


# --- session roles (build-vet-loop §1.3) -------------------------------------------------------
# A work-item's turns run in ROLE-keyed sessions — the boundary sits where a fresh PERSPECTIVE is
# required, not where a phase label changes: `intake` narrates (one thread per item, repo cwd),
# `build` remembers (persists across build⟷vet cycles, worktree cwd), `vet` forgets (fresh per
# cycle — step-4 mechanics; worktree cwd). The map is explicit CODE (§4.5.1), replacing the old
# implicit rotate-on-cwd-change accident that made build+vet+review+close share one session.
SESSION_ROLES: tuple[str, ...] = ("intake", "build", "vet")

# The durable `session.kind` values (spine column) — the stampable superset: the item ROLES above
# + `general` (un-bound advisor session) + `onboarding` (label-only kind: stamped at birth for the
# picker's category chip, but the onboarding persona is applied per-turn by project state, never by
# this stamp) + `diagnosis` (read-only inspector pointed at a subject run — the one kind that
# changes runtime behavior). `work_item` is never stamped — it's the DERIVED label for legacy
# pre-roles item sessions (item_id set, kind NULL); the item_id stamp, not the kind, is what makes
# a session item-bound. Each kind's identity PREAMBLE lives in core/kernel_speech.py.
SESSION_KINDS = ("general", "work_item", "onboarding", "diagnosis", "intake", "build", "vet")

# Of those, the ones that are the AGENTS' OWN threads rather than the owner's: `build` and `vet` run
# headless in a worktree (background turns, denied approval, no chat surface) — working memory, not
# conversation. The owner cannot open one, cannot answer in one, and never sees it in the session
# picker; counting them as "sessions" made the repo tile disagree with the list on screen. Their work
# reaches the owner as artifacts and the run trace instead.
# An UNKNOWN kind reads as a conversation: a new kind that HAS a chat surface must appear (the count
# self-flags), while a new headless one is a deliberate addition that registers itself here.
AGENT_THREAD_KINDS: tuple[str, ...] = ("build", "vet")


def is_conversation(kind: str | None) -> bool:
    """Whether a session is one the owner can open and take a turn in (vs an agent's own thread)."""
    return (kind or "") not in AGENT_THREAD_KINDS

_ROLE_FOR_PHASE: dict[str, str] = {
    "triage": "intake", "plan": "intake", "review": "intake", "close": "intake",
    "build": "build",
    "vet": "vet",
    # research: no fresh-perspective boundary anywhere — one intake thread end to end.
    "investigate": "intake",
}


def session_role(phase: str | None) -> str:
    """The session ROLE a phase's turns run in. Unknown phases fail LOUD (mirrors get_profile —
    a typo must not silently land a turn in the wrong session)."""
    p = phase or "triage"
    if p not in _ROLE_FOR_PHASE:
        raise KeyError(f"phase {p!r} has no session role — known: {sorted(_ROLE_FOR_PHASE)}")
    return _ROLE_FOR_PHASE[p]


def role_uses_worktree(role: str) -> bool:
    """Whether a role's turns run at the item's WORKTREE cwd (build/vet) vs the repo (intake).
    intake stays repo-level even while a worktree exists — close merges into main, and the CLI's
    per-cwd transcript storage means the intake thread must never change cwd mid-life."""
    return role in ("build", "vet")


def get_profile(kind: str | None) -> KindProfile:
    """The profile for `kind` — LOUD KeyError on an unknown kind (D1: never a silent default).
    A missing/null kind (pre-workflow item) reads as DEFAULT_KIND for backward compatibility."""
    k = kind or DEFAULT_KIND
    if k not in KIND_PROFILES:
        raise KeyError(
            f"unknown work-item kind {k!r} — known kinds: {sorted(KIND_PROFILES)} "
            "(add a KIND_PROFILES entry to introduce a new kind)"
        )
    return KIND_PROFILES[k]


def next_phase(kind: str | None, phase: str | None) -> str | None:
    """The phase after `phase` in this kind's pipeline, or None at the last phase.
    An unknown PHASE for the kind raises loud (a research item can't sit in `build`)."""
    prof = get_profile(kind)
    p = phase or prof.phases[0]
    if p not in prof.phases:
        raise KeyError(f"phase {p!r} is not in the {prof.kind!r} pipeline {prof.phases}")
    i = prof.phases.index(p)
    return prof.phases[i + 1] if i + 1 < len(prof.phases) else None


def is_final_phase(kind: str | None, phase: str | None) -> bool:
    """True when `phase` is the kind's last pipeline stage (close)."""
    return next_phase(kind, phase) is None
