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
            "review": ("review",),
        },
        # `review` is the phase's agent-facing record — close reads what it settled, the landing
        # commit reads its `**Delivered:**`, and a revision cycle reads what it may not re-open.
        required_artifacts=("plan", "review"),
        # CLOSE RE-ADJUDICATES NOTHING. Review's exit is the lock-in (§2.3): the merge lands, and
        # from that instant code + git cannot change — so every question about the WORK was already
        # answered at the last gate where the owner could act on the answer. What survives here is
        # only what close itself can still fix, or what genuinely became true after review.
        #
        # Retired for that reason (2026-07-30, dogfood D5):
        #   `evidence_fresh`         — verification is the loop's gate and review's fact. Re-checked
        #                              after the merge it could only ever refuse the paperwork for a
        #                              decision already made, and its whole-repo fingerprint read a
        #                              freshness-sync commit as "the code moved", wedging items whose
        #                              own code was untouched and whose close phase cannot re-run a
        #                              test by design.
        #   `knowledge_row_resolved` — close AUTHORS the anchor-doc ops (slice 5); a phase's own
        #                              output cannot also be its entry condition.
        # Nor a merge check: review's EXIT is the merge (`advance_item` runs `review_merge` and 409s
        # on conflict), so an item cannot reach close unmerged.
        # `children_terminal` LEFT TOO (owner, 2026-08-09) — it is a REVIEW-gate check now. A child
        # is spawned from this item and is part of its work, so the parent must still be re-workable
        # when the child lands: re-checked, revised, re-vetted. At close it is none of those, the
        # branch being merged and the sessions closed. Close was also the ONLY gate that ever asked,
        # so a parent could pass review, land, and first meet its open child where nothing can act.
        # Close is left asking one thing — do the files exist. It wraps up finished work; it does
        # not decide whether the work finished.
        close_criteria=(),
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
            "review": ("review",),
        },
        required_artifacts=("plan", "investigation", "review"),
        # NO close criteria (owner's standing rule, 2026-08-09). `findings_delivered` and
        # `spawns_exist` lived here and both asked at the wrong phase: the first re-judged the
        # owner's report after the item was locked, and the second told the owner to "run itemize"
        # at a phase whose sessions are closed. Both read review-phase output, so both are review-
        # gate checks now (`gate_briefs.research_readiness`) — refusable where a person can answer.
        # Close wraps up finished work; it does not decide whether the work finished.
        close_criteria=(),
    ),
}

DEFAULT_KIND = "implementation"

# Every phase any kind can be in (schema Literal mirrors this — keep in sync with
# daemon/schemas/common.py WorkPhase).
ALL_PHASES: tuple[str, ...] = tuple(dict.fromkeys(
    p for prof in KIND_PROFILES.values() for p in prof.phases
))


# --- session roles (build-vet-loop §1.3) -------------------------------------------------------
# A work-item's turns run in ROLE-keyed sessions: `intake` (repo cwd), `build` (persists across
# build⟷vet cycles, worktree cwd), `vet` (fresh per cycle — step-4 mechanics; worktree cwd). The
# map is explicit CODE (§4.5.1), replacing the old implicit rotate-on-cwd-change accident that
# made build+vet+review+close share one session.
#
# WHAT `intake` IS, PRECISELY (owner, 2026-08-09). It is the SLOT four phases write to, not one
# continuous thread they share. Each background intake phase opens a fresh CLI session
# (`services/runs.py` passes `resume=None`), stores it in `session_intake`, and retires the one it
# replaced — so triage, plan and review each get their own thread, and only the newest survives.
# The docs here used to say "one thread per item", which was never true and quietly promised a
# continuity nothing delivered.
#
# THAT SEPARATION IS THE DESIGN, not a defect to fix. Every phase reads its inputs from ARTIFACTS,
# never from transcript memory: plan gets triage's conclusions from `brief.md` — the reviewed,
# structured version — which beats recall. A shared thread would also carry triage's wrong turns
# into plan, and anchoring is a real cost (it is exactly why `vet` forgets). A useful side effect:
# fill never accumulates, so an item's threads rarely approach the compaction trigger.
#
# THE ONE THING IT COSTS is the OWNER's words. Anything they said mid-phase dies with that thread;
# it reaches the next phase only because the phase agent wrote it into the artifact. That is why
# `## From you` (brief) and `## Decisions & clarifications` (plan) are carried forward MECHANICALLY
# rather than left to an agent to remember — see `artifacts.carry_owner_input`.
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
    # These four share the intake SLOT, not a thread — each opens its own and retires the last.
    "triage": "intake", "plan": "intake", "review": "intake", "close": "intake",
    "build": "build",
    "vet": "vet",
    "investigate": "intake",
}


def session_role(phase: str | None) -> str:
    """The session-slot ROLE a phase's turns are stored under. Unknown phases fail LOUD (mirrors
    get_profile — a typo must not silently land a turn in the wrong slot).

    NOT a promise of shared context: two phases with the same role hold the slot in turn. See the
    block above for why that is deliberate and what it costs."""
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
