"""Response schemas for the drilldown + lifecycle routes (routers/dev/gates.py — §4/D8).

The GATE BRIEF is gone (renovation v2 slice 6): a markdown narrative that embedded a truncated copy
of the phase artifact and closed with a recommendation. What replaced it is typed all the way down —
`DrilldownResponse` — and the shapes here are deliberately dumb: every field is either a fact
computed in `core/` or a decision made in `services/drilldown.py`. Nothing is derived at render time.
"""

from __future__ import annotations

from pydantic import BaseModel


class GateCheck(BaseModel):
    """One mechanical row of a gate's evaluation — computed from durable state, never a claim.

    `blocking` is the must-resolve marker (§2.1): a FAILING blocking check greys Approve, and every
    other row is a named, visible fact the owner may act over with their eyes open. Both halves
    matter — the old surface rendered checks as coloured dots with the reason hidden in a `title`
    attribute, so a red gate looked identical whether it was fatal or advisory."""
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
    """Why an item is parked for the owner when it isn't a plain gate wait — a deputy escalation, a
    build⟷vet halt, or a blocked run. None ⇒ a normal gate."""
    source: str          # deputy | loop | agent
    gate: str | None     # the gate the deputy escalated, when source == deputy
    headline: str        # one line: who paged and why
    detail: str          # the runbook / blocker narrative (escalation text or blocked run summary)
    next: str | None     # the decision the owner owes, in the reporter's own words


class AuthorizationRequest(BaseModel):
    """A contract change a work-item couldn't self-authorize (BV-A2), awaiting the owner's grant or
    deny at the review gate. `delegable` = whether the deputy COULD have granted it (a sync-to-
    reality scope) vs it being owner-reserved (why it reached you). The owner grants regardless."""
    id: str
    what: str
    why: str
    doc: str
    scope: str
    delegable: bool


class DrilldownAction(BaseModel):
    """One control, with its activation decided SERVER-SIDE (§4's universal rule + the owner's slice-6
    input). `reason` is populated either way: greyed it says what would make it live, live it says
    what clicking does. `home` places it — `actions` (the frame's bar) or `git` (the Git tab).

    Never hide a control: a `fast` repo with no PR button anywhere read as a missing feature, with
    nothing on screen saying why."""
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
    """One open sub-item the parent is waiting on — resolved to something the owner can read and go
    to. `close_readiness` reports these as a comma-joined string of ids, which names a thing without
    saying what it is or how far along it got (owner, 2026-08-09)."""
    id: str
    title: str
    phase: str
    status: str


class AttentionCard(BaseModel):
    """§4.2's WHAT-YOU-NEED-TO-DO card — the drilldown's single most important element, and the answer
    to "I opened this and don't know what's needed from me". Three connected parts: WHY (the back
    story) · DO (the exact act + the one control that performs it) · BASIS (pointers to what decides
    it). None when nothing needs the owner — the card is hidden entirely, never an empty shell."""
    kind: str              # question | escalation | paged | review | gate | awaiting_child
    why: str
    detail: str
    do: str
    click: str             # the action id that performs it ('chat' = the item's chat rail)
    basis: list[str]
    questions: list[AskQuestion]
    #: The open sub-items holding this one — non-empty only on an `awaiting_child` card, where the
    #: card asks nothing of the owner and exists purely to say what it is waiting on.
    children: list[BlockingChild] = []


class NowStrip(BaseModel):
    """What is happening right now: the live phase + cycle, and what that phase concluded.

    It also carried `last` — the newest event's own sentence. That line was cut (owner,
    2026-08-08): every version of it restated something already on the card. "Deputy escalated the
    review gate to you" sat one inch above the attention card that says the same thing in full, and
    the phase name and the running dot answer "where is this" without it."""
    phase: str
    cycle: int
    running: bool
    # A phase's `**Summary:**` line — this phase's own once it has written one, and until then the
    # last COMPLETED phase's, so the card is never blank while work is in flight.
    summary: str = ""
    #: Which phase concluded `summary`. Equal to `phase` when it is this phase's own; an earlier
    #: phase while this one is still working, so the surface labels it instead of passing it off.
    summary_phase: str = ""


class AboutRow(BaseModel):
    """One row of `About this work-item` — what this item IS, in the owner's own framing. A LIST of
    these, not an object: the order (what it is → where it came from → what it's for) is the
    meaning, and an empty row is dropped server-side rather than rendered blank."""
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
    """One standing lens's read of the current cycle. `probed` carries weight even with no
    findings: it is the difference between "nothing is wrong here" and "nobody looked" — so it is a
    LIST, one probe per entry, and the surface renders it as one. A paragraph hides how many
    distinct things were actually tried, which is the only number a reader can judge it by."""
    lens: str               # intent | safety | robustness | performance
    probed: list[str] = []
    findings: list[LensFinding] = []
    cycle: int | None = None


class ProofVerdict(BaseModel):
    """One check of the plan's exam: what it will prove (`proves` in the owner's terms, `expect` in
    the machine's), and where it stands. `ran` False ⇒ the loop hasn't reached it yet — the row
    exists from the plan gate on, so the owner approving a plan can see the proof they are
    approving. `result` is the vet's captured output, verbatim — a failing row IS the
    expected-vs-actual."""
    check: str
    # The plan's one plain sentence for what is true of the product when this passes. Empty only on
    # a recorded check the current plan no longer declares (a revision dropped it).
    proves: str = ""
    expect: str = ""
    mode: str = ""
    ran: bool = False
    # Provenance: `machine` = the kernel ran the check's `run:` block, `agent` = a vetter attested.
    # On a row that hasn't run it is the plan's promise of which one it will be.
    by: str = "agent"
    # Where the check came from: "" = authored for this item, `standing`/`library` = inherited from
    # the repo's verification library. `proof_rows` has emitted it since the library shipped; the
    # response model dropped it on the floor until the Task tab's drawer went looking for it.
    source: str = ""
    passed: bool
    deferred: bool
    cycle: int | None
    how: str
    result: str
    # Vet's reading of a failure (design §5) — where it broke, why, and what it could not
    # determine. Empty on a passing check, and on a cause from an earlier cycle: a located cause
    # the code has already moved past misleads more than silence.
    where: str = ""
    why: str = ""
    unknown: str = ""
    # The criteria the PLAN set (readable at the plan gate, before anything runs) and the judgment
    # recorded against them. Both empty on a check with no rubric.
    rubric: list[str] = []
    criteria: list[Criterion] = []
    history: list[VerdictHistory] = []


class ProofRow(BaseModel):
    """§4.2's connected view: one row per BUILT THING, each carrying its own validation →
    verification. The join key is the plan task id — cycle §Built/§Validation bullets lead with it and
    vet-plan checks name it in `covers:`. `task: ""` is the item-wide row, where untagged content
    lands: nothing is dropped and nothing is guessed at."""
    task: str
    #: the task's NAME — the plan's head line, what the Task tab shows at full contrast.
    text: str
    #: the SPECIFICATION under it — the plan's indented continuation, written for whoever
    #: implements the task. Folded away in the surface: it is evidence, not the label.
    detail: str = ""
    done: bool
    built: list[str]
    validated: list[str]
    verified: list[ProofVerdict]


class DrilldownResponse(BaseModel):
    """Everything the work-item drilldown renders, computed once per poll. One route instead of four,
    because every tab reads the same item folder — and one computation of the gate's checks, shared
    with the deputy's prompt, because two summaries of one gate is how the owner loses the ability to
    check the deputy's call."""
    id: str
    phase: str
    gate: str            # triage-exit | pre-main | review | close
    gate_label: str
    at_gate: bool        # False ⇒ mid-phase; the payload describes the NEXT gate
    terminal: bool
    now: NowStrip
    attention: AttentionCard | None
    # `About this work-item` — what a reader opening a strange item needs before anything else.
    # It replaced `glance` (Goal · Progress), which restated the title in the header above it and
    # the tasks/checks the Task tab renders in full. Server-composed rows, rendered in order; the
    # FE reads no label by name, so adding one is a one-line change here.
    about: list[AboutRow]
    checks: list[GateCheck]
    blocked_by: list[str]   # empty ⇔ Approve is live
    numbers: GateNumbers
    authorizations: list[AuthorizationRequest]
    paged: PagedNotice | None
    actions: list[DrilldownAction]
    proof: list[ProofRow]
    lenses: list[LensRead]
    reports: list[str]      # phases that have a report to read; the rest grey out


class PhaseReportResponse(BaseModel):
    """One phase's user-facing report for the Reports tab — the markdown 1:1, plus the path to the
    full agent-facing contract behind it (§4.3's "Open full contract"). The report is the compact
    read; the contract is the whole thing, one click away, never pasted in."""
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
    """One thing the owner wants proven. Each becomes a check in the plan's `## Verification plan`,
    its `proves:` written in their words — which is why it is one slot, not a paragraph."""
    description: str


class OwnerInputResponse(BaseModel):
    """`reports/report-triage.md` § From you — the one section of any report the OWNER writes, read
    back from disk after every save so the surface shows what plan will actually read. `exists` is
    whether the triage brief is on disk at all: before triage runs there is nothing to write into.

    SLOTS, not prose: one reference and one note per entry, so each can be added and removed on its
    own and the plan phase's "one note, one check" rule matches what is on disk."""
    exists: bool
    references: list[OwnerReference]
    notes: list[OwnerNote]


class AbandonResponse(BaseModel):
    """The abandon brief (D8): what was torn down + the children triage list. Blocking children
    existed only for this parent — the owner disposes each (abandon / promote to independent);
    parallel children continue untouched."""
    ok: bool
    id: str
    outcome: str   # abandoned | superseded
    worktree_removed: bool | None = None
    session_cleared: bool
    runs_freed: int
    blocking_children: list[str]
    parallel_children: list[str]
