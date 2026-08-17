"""Response schemas for the general/ anchor-doc routes (routers/dev/general.py) + the roadmap board.

The anchor docs (project-prd · spec · roadmap · architecture · resources) are plain markdown with NO
frontmatter; the roadmap board is the deliverable → wave → work-item-instance join, assembled from the
readable deliverable/wave lists + each item's own `wave:`/`deliverable:` pointer (see
`core.dev_knowledge.roadmap_board`)."""

from __future__ import annotations

from pydantic import BaseModel

from ..common import WorkPhase, WorkStatus


class GeneralDoc(BaseModel):
    name: str
    present: bool


class GeneralDocsResponse(BaseModel):
    docs: list[GeneralDoc]


class ProjectStatusResponse(BaseModel):
    """Whether this project's memory is established (PRD defines ≥1 deliverable). The dev workspace
    gates on it: an un-established repo shows the onboarding front door instead of the work tabs.
    `onboard_mode` is the connect-time choice (project-init | retrofit) the front door launches
    directly; null ⇒ the repo predates connect-flow, so the landing offers both paths to pick."""
    established: bool
    onboard_mode: str | None = None
    docs: list[GeneralDoc]


class GeneralDocResponse(BaseModel):
    name: str
    content: str | None = None  # None = the file doesn't exist yet


class GeneralDocSaveBody(BaseModel):
    context_id: str = "global"
    content: str


class GeneralDocSaveResponse(BaseModel):
    ok: bool
    name: str


# --- the verification library (verification-model §8) ---------------------------

class LibraryEntry(BaseModel):
    """One proven check the repo keeps. `standing` entries are attached to every implementation
    item's plan; `available` ones are cited by id. Same fields as a plan's check, because a plan
    inherits the entry verbatim."""
    id: str
    tier: str
    proves: str = ""
    traces: str = ""
    mode: str = ""
    scenario: str = ""
    run: str = ""
    expect: str = ""
    rubric: list[str] = []


class VerificationLibraryResponse(BaseModel):
    standing: list[LibraryEntry]
    available: list[LibraryEntry]


class DecisionEntry(BaseModel):
    id: str            # D-NNN — the grep anchor and supersession target, never reused
    title: str
    status: str        # accepted | superseded by D-NNN | deprecated (inline in the heading)
    body: str          # Date · Decision · Why · Rejected · Source, verbatim


class DecisionsResponse(BaseModel):
    decisions: list[DecisionEntry]


class LibraryEntryBody(BaseModel):
    context_id: str = "global"
    tier: str          # standing | available — promoting is the owner's call and nobody else's


# --- roadmap board (deliverable → wave → item join) -----------------------------

class Rollup(BaseModel):
    done: int
    total: int


class BoardItem(BaseModel):
    """A work-item as it appears on the board — status + dates are the item's own, rendered live."""
    id: str
    title: str | None = None
    phase: WorkPhase | None = None
    status: WorkStatus | None = None
    done_at: str | None = None
    date: str | None = None  # the one relevant date (done_at, else updated/created), as a string


class BoardWave(BaseModel):
    id: str
    title: str
    deliverable: str
    status: str | None = None  # curated glyph: done | active | planned
    items: list[BoardItem]
    rollup: Rollup


class BoardDeliverable(BaseModel):
    id: str
    title: str
    # Typed sub-fields from the PRD (both optional — a pre-typed PRD leaves them empty).
    value: str | None = None   # what the owner can DO once this lands
    needs: list[str] = []      # d-ids this depends on
    waves: list[BoardWave]
    items: list[BoardItem]  # items pointing directly at the deliverable (no wave)
    rollup: Rollup


class Orphan(BaseModel):
    """A referential-integrity break — a pointer to an id the anchor docs don't define."""
    reason: str  # wave-deliverable | item-deliverable | item-wave
    wave: str | None = None
    deliverable: str | None = None
    items: list[str] | None = None


class RoadmapBoardResponse(BaseModel):
    deliverables: list[BoardDeliverable]
    orphans: list[Orphan]


# --- the portrait (what the project IS) -----------------------------------------
# Six bands, each fed by exactly ONE anchor doc, so the view can never become a place knowledge
# secretly lives. Everything the dashboard already answers — decisions, work in motion, lint,
# status — is deliberately absent: this is the portrait, not the console.

class PortraitIdentity(BaseModel):
    """project-prd — what it is, who for, and the load-bearing field: why it exists."""
    name: str
    one_liner: str | None = None
    who: str | None = None
    why: str | None = None


class PortraitGoals(BaseModel):
    """project-prd. `now` must be specific; `direction` is allowed to be directional — the horizon
    split dissolves the vision-vs-concreteness argument instead of losing it. Non-goals carry equal
    weight: what a project refuses is as defining as what it pursues."""
    now: list[str] = []
    direction: list[str] = []
    non_goals: list[str] = []


class PortraitCapability(BaseModel):
    """capabilities — one thing the system can do RIGHT NOW, in user language."""
    name: str
    detail: str | None = None


class StackRow(BaseModel):
    key: str
    value: str


class PortraitBuild(BaseModel):
    """architecture — only what code CAN'T say. No file trees, no component inventories: those are
    stale duplicates by construction. Invariants, boundaries, rationale, and the refusals."""
    stack: list[StackRow] = []
    invariants: list[str] = []
    not_here: list[str] = []


class PortraitDeliverable(BaseModel):
    """project-prd — the value statement, not the work. `delivered` is project-level truth (this
    value exists now / doesn't yet); sequencing and progress live in the pipeline."""
    id: str
    value: str | None = None
    title: str
    delivered: bool = False


class PortraitResponse(BaseModel):
    identity: PortraitIdentity
    goals: PortraitGoals
    capabilities: list[PortraitCapability] = []
    build: PortraitBuild
    deliverables: list[PortraitDeliverable] = []
    resources: list[StackRow] = []


class LintFinding(BaseModel):
    """One actionable finding over general/. Derived on read — never stored, so it can't go stale."""
    severity: str          # error | warn | info
    kind: str              # missing-doc | deliverable-unclaimed | broken-needs | no-success-signal |
    #                        signal-orphan | open-questions | stale-doc
    detail: str            # a full sentence the owner can act on
    ref: str | None = None  # the doc name or d-id it points at


class LintResponse(BaseModel):
    findings: list[LintFinding]
    counts: dict[str, int]
