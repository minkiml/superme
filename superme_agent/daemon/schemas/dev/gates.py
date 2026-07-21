"""Response schemas for the gate-brief + lifecycle routes (routers/dev/gates.py — S6/D8/D10)."""

from __future__ import annotations

from pydantic import BaseModel


class GateCheck(BaseModel):
    """One mechanical row of a gate's evaluation — computed from durable state, never a claim."""
    criterion: str
    ok: bool
    detail: str


class GateOption(BaseModel):
    id: str
    label: str
    consequence: str


class GateDecision(BaseModel):
    """The uniform decision block (D10 ★): recommendation FIRST, stakes one line, per-option
    consequence, dual-scale effort."""
    recommendation: str
    stakes: str
    options: list[GateOption]
    effort_user: str
    effort_agent: str


class GateFact(BaseModel):
    """One label:value row of the typed NOW card (renovation §3) — a present fact, never prose.
    `tone` tints the row ('' neutral · 'warn')."""
    label: str
    value: str
    tone: str = ""


class GateNumbers(BaseModel):
    """The brief's real-ratio counts (law 2: counts of real things, never invented scores)."""
    tasks_done: int
    tasks_total: int
    cycle: int
    checks_pass: int
    checks_total: int


class LoopCycle(BaseModel):
    """One filed vet report's verdict column: check id → pass/fail."""
    cycle: int
    verdicts: dict[str, bool]


class LoopAttempt(BaseModel):
    """One loop-driver decision from the attempts ledger."""
    cycle: int
    decision: str
    reason: str
    ts: str


class LoopInstruments(BaseModel):
    """The build⟷vet loop's live panel (renovation §2 Loop row) — the checks×cycles matrix,
    the newest failing report's Findings verbatim, and the driver trail. All derived from the
    ledger + reports the loop already writes; empty checks+cycles ⇒ nothing to show."""
    checks: list[str]
    cycles: list[LoopCycle]
    findings: str | None
    attempts: list[LoopAttempt]


class PagedNotice(BaseModel):
    """Why an item is parked for the owner when it isn't a plain gate wait — a deputy escalation, a
    build⟷vet halt, or a blocked run. The drilldown LEADS with this so 'what's going on / what do I
    decide' is answered on arrival, not buried under a future-gate preview. None ⇒ a normal gate."""
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


class GateBriefResponse(BaseModel):
    """One gate's full decision surface. `brief` is the rendered markdown (continuity → delta →
    narrative → decision) — since the typed fields landed it is the collapsed DETAILS payload;
    the NOW card renders facts/assumptions/flags/numbers + the embedded gate report.
    `at_gate: False` means the item is mid-phase and this previews the NEXT gate. Answerable
    without opening code — that is the contract."""
    id: str
    gate: str      # triage-exit | pre-main | review | close
    at_gate: bool
    phase: str
    title: str
    brief: str
    decision: GateDecision
    checks: list[GateCheck]
    facts: list[GateFact]
    assumptions: list[str]   # pre-main: plan.md `## Risks & assumptions`, verbatim
    flags: list[str]         # soft judgment flags (e.g. vague vet `expect`s) — never blocking
    numbers: GateNumbers
    report_html: str | None  # the gate's generated report (self-contained html), embed-ready
    loop: LoopInstruments    # the build⟷vet panel; empty checks+cycles ⇒ hidden
    snippet: str | None      # review: newest passing ledger entry verbatim (captured reality)
    terminal: bool           # done/abandoned — the surface collapses the decision entirely
    paged: PagedNotice | None  # why it's parked (escalation/halt/block) — leads the drilldown; None = plain gate
    authorizations: list[AuthorizationRequest]  # review (BV-A2): deferred contract changes awaiting grant/deny


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
