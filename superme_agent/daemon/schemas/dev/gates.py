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


class AttentionCard(BaseModel):
    """§4.2's WHAT-YOU-NEED-TO-DO card — the drilldown's single most important element, and the answer
    to "I opened this and don't know what's needed from me". Three connected parts: WHY (the back
    story) · DO (the exact act + the one control that performs it) · BASIS (pointers to what decides
    it). None when nothing needs the owner — the card is hidden entirely, never an empty shell."""
    kind: str              # question | escalation | paged | review | gate
    why: str
    detail: str
    do: str
    click: str             # the action id that performs it ('chat' = the item's chat rail)
    basis: list[str]
    questions: list[AskQuestion]


class NowStrip(BaseModel):
    """What is happening right now: the live phase + cycle, and the newest recorded event."""
    phase: str
    cycle: int
    running: bool
    last: str


class VerdictHistory(BaseModel):
    """One cycle's verdict for a check — the sequence renders as `c3 ✗→✓`."""
    cycle: int | None
    passed: bool


class ProofVerdict(BaseModel):
    """One check of the plan's exam: what it will prove (`expect`, `mode`), and where it stands.
    `ran` False ⇒ the loop hasn't reached it yet — the row exists from the plan gate on, so the
    owner approving a plan can see the proof they are approving. `result` is the vet's captured
    output, verbatim — a failing row IS the expected-vs-actual."""
    check: str
    expect: str = ""
    mode: str = ""
    ran: bool = False
    # Provenance: `machine` = the kernel ran the check's `run:` block, `agent` = a vetter attested.
    # On a row that hasn't run it is the plan's promise of which one it will be.
    by: str = "agent"
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
    history: list[VerdictHistory] = []


class ProofRow(BaseModel):
    """§4.2's connected view: one row per BUILT THING, each carrying its own validation →
    verification. The join key is the plan task id — cycle §Built/§Validation bullets lead with it and
    vet-plan checks name it in `covers:`. `task: ""` is the item-wide row, where untagged content
    lands: nothing is dropped and nothing is guessed at."""
    task: str
    text: str
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
    # The status strip — a strip, not a feed (the feed is Trace). An ORDERED label→value map, not
    # fixed fields: the server composes both halves, so adding or dropping a row (`next` went on
    # 2026-07-31) is a one-line change in `_glance` instead of an edit in four places that fails as
    # a 500 if any one of them is missed. The FE renders the entries in order and reads no key by
    # name — the labels ARE the contract's payload, not its shape.
    glance: dict[str, str]
    checks: list[GateCheck]
    blocked_by: list[str]   # empty ⇔ Approve is live
    numbers: GateNumbers
    authorizations: list[AuthorizationRequest]
    paged: PagedNotice | None
    actions: list[DrilldownAction]
    proof: list[ProofRow]
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
