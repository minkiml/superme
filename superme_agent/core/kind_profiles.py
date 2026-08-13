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
        # NO PLAN PHASE (research-sweep-model-design §3, 2026-08-13). Plan's real product on the
        # implementation path is the VET-PLAN — the Done criteria build is measured against. Research
        # has no vet, and subtracting that leaves only "decide the approach", which the family guide
        # (`investigate/references/<family>.md`) already owns in far more detail than a plan session
        # would write. The split also cost real continuity: the plan session's UNDERSTANDING never
        # transferred to investigate, only its document did — a price implementation pays willingly
        # because build must be measured against something written first, and research got nothing
        # for. Research's phases were originally implementation's minus build+vet and nobody asked
        # whether plan survived the subtraction; this is that question, answered.
        phases=("triage", "investigate", "review", "close"),
        worktree=False,
        knowledge_writes=False,
        emits={
            "triage": ("brief",),
            "investigate": ("investigation",),
            "review": ("review",),
        },
        required_artifacts=("investigation", "review"),
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

# --- item SCALE: how much ceremony this item's work is worth --------------------------------------
# Kind decides the MACHINERY; scale decides how much CONTENT that machinery moves. They are
# deliberately different axes, and scale is not a kind: a small item still gets the full pipeline,
# its own branch, numbered tasks, a commit per task and a merge (owner, 2026-08-10). Nothing about
# the STRUCTURE moves — every phase runs, every document keeps every section. What changes is how
# much each phase reads before writing and how much it writes.
#
# Measured baseline that makes this worth having (2026-08-10, five playground items through the
# full loop): triage 70k tokens over ~12 tool calls, plan 61k over ~14, review 52k over ~16 — so the
# intake phases together cost MORE than build+vet, and they run about twice per item. The cost
# tracks tool calls, not the launch prompt (that is ~2k chars). A one-line fix pays all of it.
#
# TWO VALUES ON PURPOSE. `standard` is exactly today's behaviour, so nothing in flight changes
# meaning. A third tier would only give an agent a middle to reach for.
ITEM_SCALES: tuple[str, ...] = ("small", "standard")
DEFAULT_SCALE = "standard"


def item_scale(item: dict | None) -> str:
    """This item's declared scale, defaulting to `standard`. Deliberately FORGIVING where
    `get_profile` is loud: an unknown kind means the item would run through the wrong machinery, but
    an unknown or absent scale only means nobody judged it — and every item minted before the field
    existed is in exactly that position. Defaulting them to today's behaviour is the correct
    reading, not a swallowed error."""
    scale = str((item or {}).get("scale") or "").strip()
    return scale if scale in ITEM_SCALES else DEFAULT_SCALE


# --- RESEARCH KIND: which family of investigation a research item is ------------------------------
# A third axis, and it applies to ONE kind. `kind` decides the machinery, `scale` decides how much
# content moves through it, and this decides what counts as an answer — which is why it routes two
# real things rather than being a label: the guide the investigate phase reads, and the artifact
# shape it scaffolds (`study` has its own; see artifacts._TEMPLATE_HOMES).
#
# NO DEFAULT, on purpose, and this is where it differs from `scale`. `standard` is a real behaviour
# every skill already describes, so an unjudged item has somewhere honest to sit. There is no
# equivalent family — an unjudged research item has not been told what counts as an answer, and
# inventing one for it would silently pick a bar and an artifact. Unset reads None, the skill falls
# back to naming the family in prose, and the base artifact shape (which is the audit shape, the one
# the phase was originally written for) is what gets scaffolded.
# The six (owner, 2026-08-13). Four are audit-shaped and differ in what they LOOK for and what
# severity means in each; `study` and `refactoring` both end in a proposal; `deep-diagnosis` ends in
# a mechanism.
#
# `deep-diagnosis` is NOT `diagnosis` on purpose. A `diagnosis` SESSION already exists — the quick
# read of one run's trace, launched from an Activity row (sessions.kind, session_kinds.py). Reusing
# that word for a work-item family would put two meanings on one token, which is how the wrong-field
# bugs start. This one is the dedicated version: a planned investigation with a gate, not a look.
#
# `measurement` was here and is NOT: performance belongs inside the general audit, and a number
# without a re-runnable recipe is a bad receipt in every family, not a family of its own.
RESEARCH_KINDS: tuple[str, ...] = ("audit", "refactoring", "housekeeping", "security",
                                   "study", "deep-diagnosis")

# The families whose guide PRESCRIBES fan-out — each `references/<family>.md` carries a `## Fan-out`
# section telling investigate to split the surface across subagents (by area, or by boundary for
# security). These four are the whole-codebase families: their subject is large by definition.
#
# WHY THIS TUPLE EXISTS AT ALL (measured, 2026-08-13). Across seven live items — including a
# whole-repo refactoring study that burned 127k tokens and an audit of every reporting command —
# **not one subagent was ever spawned.** The instruction was in all four guides, phrased clearly, and
# complied with 0% of the time. That is the prompt-quality pass's own law restated: compliance tracks
# ENFORCEMENT, not emphasis — `<fill:…>` slots are gate-checked and leak 0%, a thrice-stated prose
# note leaked 100%. `## Fan-out` was prose nothing checked, so nothing did it.
#
# `study` and `deep-diagnosis` are absent deliberately: both follow ONE thread of enquiry to its end,
# and splitting a diagnosis across agents is how a causal chain gets lost.
FANOUT_FAMILIES: tuple[str, ...] = ("audit", "refactoring", "housekeeping", "security")


def research_kind(item: dict | None) -> str | None:
    """This item's investigation family, or None when nobody has judged one. Forgiving for the same
    reason `item_scale` is: every research item minted before this field existed has no line, and an
    unknown value means the judgment is missing, not that the item is broken."""
    fam = str((item or {}).get("research_kind") or "").strip()
    return fam if fam in RESEARCH_KINDS else None

# Every phase any kind can be in (schema Literal mirrors this — keep in sync with
# daemon/schemas/common.py WorkPhase).
ALL_PHASES: tuple[str, ...] = tuple(dict.fromkeys(
    p for prof in KIND_PROFILES.values() for p in prof.phases
))


# --- session slots and roles (build-vet-loop §1.3; per-phase sessions 2026-08-13) --------------
# A work-item's turns are stored in SLOTS on its own frontmatter (`session_<slot>`). There is one
# slot PER PHASE, plus `build` and `vet`:
#
#     THE RULE — a session belongs to a PHASE.
#     Entering the SAME phase again RESUMES its thread. Moving to a DIFFERENT phase MINTS a fresh one.
#
# WHAT THIS REPLACED, and why (owner, 2026-08-13). Until now the five non-build phases shared ONE
# `intake` slot: each entry minted a fresh session, stored it, and RETIRED the one it replaced. So
# review, which is the phase most often entered more than once, forgot its own previous review every
# revise round — and a send-back that resumed "the item's thread" resumed whichever phase happened to
# hold the slot, not the phase being sent back to.
#
# THE OLD REASONING STILL HOLDS, BUT ONLY WHERE IT APPLIED. Every phase reads its inputs from
# ARTIFACTS, never from transcript memory: plan gets triage's conclusions from `brief.md` — the
# reviewed, structured version — which beats recall. A shared thread would also carry triage's wrong
# turns into plan, and anchoring is a real cost (it is exactly why `vet` forgets). That is an argument
# about handing off between DIFFERENT phases, and mint-on-phase-change keeps all of it. It was never
# an argument about re-entering the SAME phase, which is one agent looking at a changed tree — the
# slot model simply could not tell the two cases apart.
#
# WHAT IT COSTS, KNOWINGLY: threads now accumulate across revise rounds instead of being purged, so a
# heavily-revised item carries more fill. Compaction exists for exactly that.
#
# THE ONE THING THE SEPARATION STILL COSTS is the OWNER's words: anything they said mid-phase reaches
# the NEXT phase only because the phase agent wrote it into the artifact. That is why `## From you`
# (brief) and `## Decisions & clarifications` (plan) are carried forward MECHANICALLY rather than left
# to an agent to remember — see `artifacts.carry_owner_input`.
#
# `intake` IS RETAINED AS A LEGACY READ SLOT and is never written again. Items in flight when this
# landed carry `session_intake`; `dev_knowledge._session_fields` falls back to it for any intake-family
# phase, so those items keep their thread and self-migrate on the next turn that writes a slot.
INTAKE_PHASES: tuple[str, ...] = ("triage", "plan", "investigate", "review", "close")
SESSION_SLOTS: tuple[str, ...] = (*INTAKE_PHASES, "build", "vet")
LEGACY_INTAKE_SLOT = "intake"

# The spine's `session.kind` grouping — UNCHANGED by the per-phase split. A slot answers "which
# thread"; a kind answers "what sort of thread", and all five intake phases are still the same sort:
# the owner's own item-facing conversation. Splitting the kind too would widen the spine enum, the
# token taxonomy, the session picker's categories and every FE label for no question anyone asks.
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
    # The spine KIND grouping, not the slot: all five intake phases are the same SORT of thread.
    "triage": "intake", "plan": "intake", "review": "intake", "close": "intake",
    "build": "build",
    "vet": "vet",
    "investigate": "intake",
}

# Slot per phase — identity for the intake family, so `sessions[phase]` IS that phase's thread.
_SLOT_FOR_PHASE: dict[str, str] = {**{p: p for p in INTAKE_PHASES}, "build": "build", "vet": "vet"}


def session_slot(phase: str | None) -> str:
    """The SLOT a phase's turns are stored in (`session_<slot>` on the item). Unknown phases fail
    LOUD (mirrors get_profile — a typo must not silently land a turn in the wrong thread).

    This is the answer to "WHICH thread": one per phase, so re-entering a phase resumes its own and
    moving to another phase mints. For "what SORT of thread" — the spine's `session.kind` — use
    `session_role`. See the block above for why the two are separate."""
    p = phase or "triage"
    if p not in _SLOT_FOR_PHASE:
        raise KeyError(f"phase {p!r} has no session slot — known: {sorted(_SLOT_FOR_PHASE)}")
    return _SLOT_FOR_PHASE[p]


def session_role(phase: str | None) -> str:
    """The spine session KIND a phase's turns are stamped with (`intake` | `build` | `vet`).
    Unknown phases fail LOUD.

    NOT the storage slot — five phases share the `intake` KIND while each keeps its OWN thread.
    Use `session_slot` whenever the question is which session to resume or write."""
    p = phase or "triage"
    if p not in _ROLE_FOR_PHASE:
        raise KeyError(f"phase {p!r} has no session role — known: {sorted(_ROLE_FOR_PHASE)}")
    return _ROLE_FOR_PHASE[p]


def role_uses_worktree(role: str) -> bool:
    """Whether turns run at the item's WORKTREE cwd (build/vet) vs the repo (every intake phase).
    Accepts either a role or a slot — `build`/`vet` name the same thing in both vocabularies, and
    every other slot is an intake phase, which stays repo-level even while a worktree exists: close
    merges into main, and the CLI's per-cwd transcript storage means an intake thread must never
    change cwd mid-life."""
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
