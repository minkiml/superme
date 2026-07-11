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
