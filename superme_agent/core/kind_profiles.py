"""KIND_PROFILES — the one in-code table mapping a work-item KIND to its machinery (workspace-
workflow D1/D2). A kind exists only when it changes MACHINERY (phases, git isolation, knowledge
writes, artifacts, close criteria) — content differences (feature/bug/refactor/docs) are all
`implementation`. Extending = adding a profile entry; an unknown kind fails LOUD (never a silent
default), so a typo can't run an item through the wrong machinery.

Item-kind is orthogonal to session-kind (daemon/session_agents.py): they join at
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
        phases=("triage", "plan", "build", "validate", "deliver", "close"),
        worktree=True,
        knowledge_writes=True,
        emits={
            "plan": ("plan",),
            "validate": ("validation",),
            "deliver": ("readiness",),
            "close": ("closeout",),
        },
        required_artifacts=("plan", "validation", "readiness", "closeout"),
        close_criteria=(
            "merged_or_logged_no_merge", "evidence_fresh", "knowledge_row_resolved",
            "closeout_verified", "children_terminal",
        ),
    ),
    "research": KindProfile(
        kind="research",
        phases=("triage", "plan", "investigate", "report", "close"),
        worktree=False,
        knowledge_writes=False,
        emits={
            "plan": ("plan",),
            "report": ("findings",),
            "close": ("closeout",),
        },
        required_artifacts=("plan", "findings", "closeout"),
        close_criteria=("findings_delivered", "spawns_exist", "closeout_verified"),
    ),
}

DEFAULT_KIND = "implementation"

# Every phase any kind can be in (schema Literal mirrors this — keep in sync with
# daemon/schemas/common.py WorkPhase).
ALL_PHASES: tuple[str, ...] = tuple(dict.fromkeys(
    p for prof in KIND_PROFILES.values() for p in prof.phases
))


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
