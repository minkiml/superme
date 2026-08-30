"""Maps a work-item kind to its machinery: phases, worktree, knowledge writes, artifacts.

Unknown kinds fail loud.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class KindProfile:
    kind: str
    # Ordered: first is triage, last is close.
    phases: tuple[str, ...]
    # Does this kind land code?
    worktree: bool
    knowledge_writes: bool
    # Detached read-only checkout: isolation without a landing.
    scratch_worktree: bool = False
    emits: dict[str, tuple[str, ...]] = field(default_factory=dict)
    required_artifacts: tuple[str, ...] = ()
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
        required_artifacts=("plan", "review"),
        # Empty on purpose: review's exit already locked code and git.
        close_criteria=(),
    ),
    "research": KindProfile(
        kind="research",
        # No plan phase: plan's product is the vet-plan, and research has no vet.
        phases=("triage", "investigate", "review", "close"),
        worktree=False,
        knowledge_writes=False,
        # Otherwise read-only rests on the permission classifier alone.
        scratch_worktree=True,
        emits={
            "triage": ("brief",),
            "investigate": ("investigation",),
            "review": ("review",),
        },
        required_artifacts=("investigation", "review"),
        close_criteria=(),
    ),
}

DEFAULT_KIND = "implementation"

# Kind decides the machinery, scale decides how much content moves through it.
ITEM_SCALES: tuple[str, ...] = ("small", "standard")
DEFAULT_SCALE = "standard"

# Whether a research surface divides across subagents. Triage's call.
ITEM_FANOUT: tuple[str, ...] = ("expected", "bounded")
DEFAULT_FANOUT = "expected"


def item_fanout(item: dict | None) -> str:
    """Whether the surface needs splitting. Absent means nobody judged."""
    v = str((item or {}).get("fanout") or "").strip()
    return v if v in ITEM_FANOUT else DEFAULT_FANOUT


def item_scale(item: dict | None) -> str:
    """This item's declared scale. Unknown means nobody judged, not broken."""
    scale = str((item or {}).get("scale") or "").strip()
    return scale if scale in ITEM_SCALES else DEFAULT_SCALE


@dataclass(frozen=True)
class ResearchFamily:
    """One row of the registry. The slug drives guide, template, gate rows and launch bar."""
    slug: str
    # Subject is the codebase → launches from a button. Else ticket-born.
    standing: bool
    # "Is this sound?" means nothing until you say sound in what.
    asks_interest: bool
    icon: str
    blurb: str


# `standing` also decides fan-out. If a family ever needs one without the other, split the field.
RESEARCH_FAMILIES: tuple[ResearchFamily, ...] = (
    ResearchFamily("audit", standing=True, asks_interest=True, icon="Radar",
                   blurb="Is a surface sound — coverage, performance, logic, or its promises?"),
    ResearchFamily("refactoring", standing=True, asks_interest=False, icon="Blocks",
                   blurb="This code is hard to work in — what shape should it be?"),
    ResearchFamily("housekeeping", standing=True, asks_interest=False, icon="Eraser",
                   blurb="What has gone stale — dead code, wrong comments, abandoned config?"),
    ResearchFamily("security", standing=True, asks_interest=False, icon="ShieldAlert",
                   blurb="What is exposed, and what path would an attacker actually walk?"),
    ResearchFamily("study", standing=False, asks_interest=False, icon="BookOpen",
                   blurb="How does someone else do this, and what should we take?"),
    ResearchFamily("deep-diagnosis", standing=False, asks_interest=False, icon="Stethoscope",
                   blurb="What is the mechanism behind a behaviour we cannot explain?"),
)

FAMILY_BY_SLUG: dict[str, ResearchFamily] = {f.slug: f for f in RESEARCH_FAMILIES}

# Two mirrors this table cannot drive, pinned in `test_research_kind`:
# `dev_tools.TriageFacts.research_kind` (a Literal) and `skills/triage/SKILL.md`.

RESEARCH_KINDS: tuple[str, ...] = tuple(f.slug for f in RESEARCH_FAMILIES)


def family_guide(slug: str) -> str:
    """Guide path for a family. The slug is the filename."""
    return f"references/{slug}.md"


def family_template(slug: str) -> str:
    """Investigation template name. Same rule: the slug is the shape."""
    return f"investigation-{slug}"


def standing_families() -> tuple[ResearchFamily, ...]:
    """Button-launchable families, in registry order."""
    return tuple(f for f in RESEARCH_FAMILIES if f.standing)

# Families whose guide prescribes fan-out. study and deep-diagnosis follow one thread.
FANOUT_FAMILIES: tuple[str, ...] = tuple(f.slug for f in RESEARCH_FAMILIES if f.standing)


def research_kind(item: dict | None) -> str | None:
    """This item's investigation family, or None when nobody judged one."""
    fam = str((item or {}).get("research_kind") or "").strip()
    return fam if fam in RESEARCH_KINDS else None

# Mirrored by schemas/common.py WorkPhase — keep in sync.
ALL_PHASES: tuple[str, ...] = tuple(dict.fromkeys(
    p for prof in KIND_PROFILES.values() for p in prof.phases
))


# A session belongs to a phase: re-entering resumes its thread, moving to another mints one.
INTAKE_PHASES: tuple[str, ...] = ("triage", "plan", "investigate", "review", "close")
SESSION_SLOTS: tuple[str, ...] = (*INTAKE_PHASES, "build", "vet")
# Read-only, never written again.
LEGACY_INTAKE_SLOT = "intake"

# A slot answers "which thread", a kind answers "what sort".
SESSION_ROLES: tuple[str, ...] = ("intake", "build", "vet")

# `work_item` is never stamped — derived label for legacy sessions.
SESSION_KINDS = ("general", "work_item", "onboarding", "diagnosis", "intake", "build", "vet")

# Headless agent threads. The owner can never open or answer in one.
AGENT_THREAD_KINDS: tuple[str, ...] = ("build", "vet")


def is_conversation(kind: str | None) -> bool:
    """Whether the owner can open this session and take a turn in it."""
    return (kind or "") not in AGENT_THREAD_KINDS

_ROLE_FOR_PHASE: dict[str, str] = {
    "triage": "intake", "plan": "intake", "review": "intake", "close": "intake",
    "build": "build",
    "vet": "vet",
    "investigate": "intake",
}

_SLOT_FOR_PHASE: dict[str, str] = {**{p: p for p in INTAKE_PHASES}, "build": "build", "vet": "vet"}


def session_slot(phase: str | None) -> str:
    """The slot a phase's turns are stored in. Unknown phases fail loud."""
    p = phase or "triage"
    if p not in _SLOT_FOR_PHASE:
        raise KeyError(f"phase {p!r} has no session slot — known: {sorted(_SLOT_FOR_PHASE)}")
    return _SLOT_FOR_PHASE[p]


def session_role(phase: str | None) -> str:
    """The spine session kind a phase is stamped with. Not the storage slot."""
    p = phase or "triage"
    if p not in _ROLE_FOR_PHASE:
        raise KeyError(f"phase {p!r} has no session role — known: {sorted(_ROLE_FOR_PHASE)}")
    return _ROLE_FOR_PHASE[p]


# Everything that reads or writes the item's code. Keyed on the phase, because four phases share
# the intake role.
WORKTREE_PHASES = ("plan", "build", "vet", "review")


def phase_uses_worktree(phase: str | None, kind: str | None = None) -> bool:
    """Worktree cwd or repo cwd. The CLI stores transcripts per cwd, so this cannot vary per run."""
    if kind and get_profile(kind).scratch_worktree:
        return True
    return (phase or "") in WORKTREE_PHASES


def get_profile(kind: str | None) -> KindProfile:
    """The profile for `kind`. Loud KeyError on an unknown one; missing reads as DEFAULT_KIND."""
    k = kind or DEFAULT_KIND
    if k not in KIND_PROFILES:
        raise KeyError(
            f"unknown work-item kind {k!r} — known kinds: {sorted(KIND_PROFILES)} "
            "(add a KIND_PROFILES entry to introduce a new kind)"
        )
    return KIND_PROFILES[k]


def next_phase(kind: str | None, phase: str | None) -> str | None:
    """The phase after `phase`, or None at the last. An unknown phase for the kind raises."""
    prof = get_profile(kind)
    p = phase or prof.phases[0]
    if p not in prof.phases:
        raise KeyError(f"phase {p!r} is not in the {prof.kind!r} pipeline {prof.phases}")
    i = prof.phases.index(p)
    return prof.phases[i + 1] if i + 1 < len(prof.phases) else None


def is_final_phase(kind: str | None, phase: str | None) -> bool:
    """True when `phase` is the kind's last pipeline stage (close)."""
    return next_phase(kind, phase) is None
