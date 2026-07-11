"""general/ anchor-doc routes: the doc set (project-prd · spec · roadmap · architecture · resources)
with read/save, and the roadmap board — deliverable → wave → its live work-item instances, joined by
each item's own `wave:`/`deliverable:` pointer. The parse/join lives in core.dev_knowledge; these are
the thin HTTP layer."""

from fastapi import APIRouter, Depends, HTTPException

from ...app_state import DevKnowledgeService, get_dev, get_spine, SystemSpine
from ...deps import dev_root as _dev_root
from ....core.dev_knowledge import ANCHOR_DOCS
from ...schemas.dev.general import (
    GeneralDocsResponse, GeneralDocResponse, GeneralDocSaveBody, GeneralDocSaveResponse,
    ProjectStatusResponse, RoadmapBoardResponse,
)

router = APIRouter()

_VALID = (*ANCHOR_DOCS, "resources")


@router.get("/dev/general", response_model=GeneralDocsResponse)
def dev_general_docs(context_id: str = "global", dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """The anchor-doc set with presence flags (project-prd · spec · roadmap · architecture · resources)."""
    return {"docs": dev.general_docs(_dev_root(context_id))}


@router.get("/dev/project-status", response_model=ProjectStatusResponse)
def dev_project_status(context_id: str = "global", dev: DevKnowledgeService = Depends(get_dev),
                       spine: SystemSpine = Depends(get_spine)) -> dict:
    """Whether this project's memory is established (its PRD defines ≥1 deliverable) + the doc-set
    presence flags + the connect-time onboarding choice. The dev workspace gates on `established`:
    false ⇒ show the onboarding front door, launching `onboard_mode` when it's set."""
    root = _dev_root(context_id)
    rc = spine.repo(context_id)
    return {
        "established": dev.project_established(root),
        "onboard_mode": rc.onboarding if rc else None,
        "docs": dev.general_docs(root),
    }


@router.get("/dev/general/{name}", response_model=GeneralDocResponse)
def dev_general_doc(name: str, context_id: str = "global",
                    dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """One anchor doc's raw markdown (content is null if the file doesn't exist yet). 404 unknown name."""
    if name not in _VALID:
        raise HTTPException(status_code=404, detail="unknown anchor doc")
    return {"name": name, "content": dev.read_general_doc(_dev_root(context_id), name)}


@router.put("/dev/general/{name}", response_model=GeneralDocSaveResponse)
def dev_general_doc_save(name: str, body: GeneralDocSaveBody,
                         dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Overwrite one anchor doc (creating its folder if needed). 404 on an unknown name."""
    if not dev.write_general_doc(_dev_root(body.context_id), name, body.content):
        raise HTTPException(status_code=404, detail="unknown anchor doc")
    return {"ok": True, "name": name}


@router.get("/dev/roadmap", response_model=RoadmapBoardResponse)
def dev_roadmap(context_id: str = "global", dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """The roadmap board: deliverable → wave → its live work-item instances + rollup, plus any
    referential-integrity orphans (an item/wave pointing at an id the anchor docs don't define)."""
    return dev.roadmap_board(_dev_root(context_id))
