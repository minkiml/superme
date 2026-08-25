"""Schemas for the drilldown and lifecycle routes.

Deliberately dumb shapes: every field is either a fact computed in `core/` or a decision made in
`services/drilldown.py`. Nothing is derived at render time.
"""

from __future__ import annotations

from pydantic import BaseModel
from .general import DecisionEntry


class GateCheck(BaseModel):
    """One mechanical row of a gate's evaluation, computed from durable state rather than claimed.

    `blocking` is the must-resolve marker: a failing one greys Approve."""
    criterion: str
    ok: bool
    detail: str
    blocking: bool = False


class GateNumbers(BaseModel):
    """The real-ratio counts (law 2: counts of real things, never invented scores)."""
    tasks_done: int
    tasks_total: int
    cycle: int
    checks_pass: int
    checks_total: int


class PagedNotice(BaseModel):
    """Why an item is parked when it is not a plain gate wait.

    An escalation, a build⟷vet halt, or a blocked run. None means a normal gate."""
    source: str          # deputy | loop | agent
    gate: str | None     # the gate the deputy escalated, when source == deputy
    headline: str        # one line: who paged and why
    detail: str          # the runbook / blocker narrative (escalation text or blocked run summary)
    next: str | None     # the decision the owner owes, in the reporter's own words


class AuthorizationRequest(BaseModel):
    """A change to what the project INTENDS that a work-item could not settle alone, awaiting the
    owner's grant or deny at review. Every scope is owner-reserved."""
    id: str
    what: str
    why: str
    doc: str
    scope: str


class DrilldownAction(BaseModel):
    """One control, with its activation decided SERVER-SIDE.

    `reason` is populated either way: greyed it says what would make it live, live it says what
    clicking does."""
    id: str        # approve | drop | plan | vet | resume | rerun | continue | force | pr | merge
    label: str
    home: str      # actions | git
    active: bool
    reason: str


class AskQuestion(BaseModel):
    """One question from a parked grill run — the ask-card's fields, as the reporting tool typed them."""
    question: str
    recommend: str = ""
    why: str = ""
    instead: str = ""


class BlockingChild(BaseModel):
    """One open sub-item the parent waits on, resolved to something the owner can read and go to.

    A joined id names a thing without saying what it is."""
    id: str
    title: str
    phase: str
    status: str


class AttentionCard(BaseModel):
    """The what-you-need-to-do card in three parts: WHY, DO, and BASIS.

    None when nothing needs the owner, so the card is hidden rather than empty."""
    kind: str              # question | escalation | paged | review | gate | awaiting_child
    why: str
    detail: str
    do: str
    click: str             # the action id that performs it ('chat' = the item's chat rail)
    basis: list[str]
    questions: list[AskQuestion]
    # The open sub-items holding this one — non-empty only on an `awaiting_child` card, which asks
    # nothing.
    children: list[BlockingChild] = []


class NowStrip(BaseModel):
    """The live phase and cycle, and what that phase concluded.

    The phase name and the running dot already say where this is, so no event sentence rides here."""
    phase: str
    cycle: int
    running: bool
    # This phase's own summary once written, and until then the last completed phase's, so the
    # card is never blank.
    summary: str = ""
    # Which phase concluded `summary`: equal to `phase` for its own, an earlier one while this
    # phase still works.
    summary_phase: str = ""


class AboutRow(BaseModel):
    """One row of `About this work-item`, in the owner's own framing.

    A LIST, not an object: the order is the meaning. An empty row is dropped server-side."""
    label: str
    value: str


class VerdictHistory(BaseModel):
    """One cycle's verdict for a check — the sequence renders as `c3 ✗→✓`."""
    cycle: int | None
    passed: bool


class Criterion(BaseModel):
    """One rubric criterion, judged. The unit exists because "3 of 4" is not a finding — WHICH one
    missed is."""
    text: str
    met: bool


class LensFinding(BaseModel):
    severity: str           # low | medium | high — severity is what decides whether it gates
    text: str


class LensRead(BaseModel):
    """One standing lens's read of the current cycle.

    `probed` separates "nothing is wrong" from "nobody looked", so it is a LIST, one probe per entry,
    rendered as one."""
    lens: str               # intent | safety | robustness | performance
    probed: list[str] = []
    findings: list[LensFinding] = []
    cycle: int | None = None


class ProofVerdict(BaseModel):
    """One check of the plan's exam: what it will prove, and where it stands.

    `ran` False means the loop has not reached it, so approving a plan shows the proof."""
    check: str
    # Empty only on a recorded check the current plan no longer declares, because a revision
    # dropped it.
    proves: str = ""
    expect: str = ""
    mode: str = ""
    ran: bool = False
    # `machine` means the kernel ran the check, `agent` that a vetter attested. On an unrun row it
    # is a promise.
    by: str = "agent"
    # "" means authored for this item; `standing` or `library` means inherited from the repo's
    # verification library.
    source: str = ""
    passed: bool
    deferred: bool
    cycle: int | None
    how: str
    result: str
    # Empty on a passing check, and on an earlier cycle's cause: a stale one misleads more than
    # silence.
    where: str = ""
    why: str = ""
    unknown: str = ""
    # The criteria the plan set, readable at the plan gate, and the judgment recorded against
    # them.
    rubric: list[str] = []
    criteria: list[Criterion] = []
    history: list[VerdictHistory] = []


class ProofRow(BaseModel):
    """One row per BUILT THING, each carrying its own validation and verification.

    `task: ""` is the item-wide row where untagged content lands, so nothing is dropped."""
    task: str
    #: the task's NAME — the plan's head line, what the Task tab shows at full contrast.
    text: str
    # The specification under the task, written for whoever implements it. Folded away: it is
    # evidence, not the label.
    detail: str = ""
    done: bool
    built: list[str]
    validated: list[str]
    verified: list[ProofVerdict]


class DrilldownResponse(BaseModel):
    """Everything the work-item drilldown renders, computed once per poll.

    One computation of the gate's checks, shared with the deputy, so the owner can check its call."""
    id: str
    phase: str
    gate: str            # triage-exit | pre-main | review | close
    gate_label: str
    at_gate: bool        # False ⇒ mid-phase; the payload describes the NEXT gate
    terminal: bool
    now: NowStrip
    attention: AttentionCard | None
    # Server-composed rows, rendered in order. The FE reads no label by name, so adding one is a
    # one-line change here.
    about: list[AboutRow]
    checks: list[GateCheck]
    blocked_by: list[str]   # empty ⇔ Approve is live
    numbers: GateNumbers
    authorizations: list[AuthorizationRequest]
    paged: PagedNotice | None
    actions: list[DrilldownAction]
    proof: list[ProofRow]
    lenses: list[LensRead]
    # Rulings this item's gate wrote into the project ledger — shown where the owner gave them.
    decisions: list[DecisionEntry]
    reports: list[str]      # phases that have a report to read; the rest grey out


class PhaseReportResponse(BaseModel):
    """One phase's user-facing report, plus the path to the agent-facing contract behind it.

    The report is the compact read; the contract is one click away, never pasted in."""
    phase: str
    name: str
    text: str
    path: str
    mtime: float
    contract: str | None    # relative path, or None where the report IS the record (review/close)


class OwnerReference(BaseModel):
    """One imported reference the owner handed the plan phase: where it is, and what it governs."""
    source: str
    description: str


class OwnerNote(BaseModel):
    """One thing the owner wants proven. Each becomes a check whose `proves:` is written in their words,
    which is why it is one slot and not a paragraph."""
    description: str


class OwnerInputResponse(BaseModel):
    """The one section of any report the OWNER writes, re-read from disk after every save.

    SLOTS, not prose, so each entry can be added and removed on its own."""
    exists: bool
    references: list[OwnerReference]
    notes: list[OwnerNote]


class AbandonResponse(BaseModel):
    """What was torn down, plus the children triage list.

    Blocking children existed only for this parent, so the owner disposes each; parallel children
    continue untouched."""
    ok: bool
    id: str
    outcome: str   # abandoned | superseded
    worktree_removed: bool | None = None
    session_cleared: bool
    runs_freed: int
    blocking_children: list[str]
    parallel_children: list[str]


class OwnerReferenceBody(BaseModel):
    source: str = ""
    description: str = ""


class OwnerNoteBody(BaseModel):
    description: str = ""


class OwnerInputBody(BaseModel):
    """The owner's `## From you` section, whole.

    Add and delete are both a PUT of the full lists, since the owner is its only writer."""
    context_id: str = "global"
    references: list[OwnerReferenceBody] = []
    notes: list[OwnerNoteBody] = []


class AbandonBody(BaseModel):
    context_id: str = "global"
    reason: str = ""
    superseded_by: str | None = None  # set → outcome `superseded` (no dangling supersedes)
