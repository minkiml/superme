"""`general/` anchor-doc routes: the doc set with read and save, plus the roadmap board.

The parse and join live in `core.dev_knowledge`; these are the thin HTTP layer.
"""

from fastapi import APIRouter, Depends, HTTPException

from ...app_state import DevKnowledgeService, get_dev, get_spine, SystemSpine
from ...deps import dev_root as _dev_root
from ....core import verification_library as _vl
from ....core.dev_knowledge import ANCHOR_DOCS, LEGACY_DOCS
from ...schemas.dev.general import (
    GeneralDocsResponse, GeneralDocResponse, GeneralDocSaveBody, GeneralDocSaveResponse,
    LibraryEntryBody, ProjectStatusResponse, RoadmapBoardResponse, PortraitResponse, LintResponse,
    DecisionsResponse,
    VerificationLibraryResponse,
)

router = APIRouter()

_VALID = (*ANCHOR_DOCS, *LEGACY_DOCS, "resources")   # legacy stays readable until it's folded in


@router.get("/dev/general", response_model=GeneralDocsResponse)
def dev_general_docs(context_id: str = "global", dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """The anchor-doc set with presence flags (project-prd · spec · roadmap · architecture · resources)."""
    return {"docs": dev.general_docs(_dev_root(context_id))}


@router.get("/dev/project-status", response_model=ProjectStatusResponse)
def dev_project_status(context_id: str = "global", dev: DevKnowledgeService = Depends(get_dev),
                       spine: SystemSpine = Depends(get_spine)) -> dict:
    """Whether this project's memory is established, with the doc-set flags and onboarding choice.

    The workspace gates on `established`: false shows the onboarding front door."""
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


@router.get("/dev/verification", response_model=VerificationLibraryResponse)
def dev_verification_library(context_id: str = "global") -> dict:
    """This repo's standing entries and the available ones a plan cites by id.

    A repo with no library reads as two empty lists, the correct starting state."""
    return _vl.read_library(_dev_root(context_id))


@router.get("/dev/decisions", response_model=DecisionsResponse)
def dev_decisions(context_id: str = "global") -> dict:
    """This repo's decision ledger — every call the owner has ruled on, newest FIRST.

    The file is append-only and reads oldest-first, so the order is flipped here and only here."""
    from ....core import decision_ledger as _dl
    return {"decisions": list(reversed(_dl.read_entries(_dev_root(context_id))))}


@router.patch("/dev/verification/{entry_id}", response_model=GeneralDocSaveResponse)
def dev_verification_move(entry_id: str, body: LibraryEntryBody) -> dict:
    """Promote an entry to standing, or demote it back to available. The OWNER'S call.

    A standing entry taxes every future item here, which is the one brake on the library accreting."""
    try:
        moved = _vl.move_entry(_dev_root(body.context_id), entry_id, body.tier)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not moved:
        raise HTTPException(status_code=404, detail="unknown library entry")
    return {"ok": True, "name": entry_id}


@router.delete("/dev/verification/{entry_id}", response_model=GeneralDocSaveResponse)
def dev_verification_drop(entry_id: str, context_id: str = "global") -> dict:
    """Drop an entry that turned out not to generalise. The library is knowledge, and knowledge that
    proved wrong is removed rather than kept with a caveat nobody reads."""
    if not _vl.drop_entry(_dev_root(context_id), entry_id):
        raise HTTPException(status_code=404, detail="unknown library entry")
    return {"ok": True, "name": entry_id}


@router.get("/dev/roadmap", response_model=RoadmapBoardResponse)
def dev_roadmap(context_id: str = "global", dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """The roadmap board: deliverable → wave → its live work-item instances + rollup, plus any
    referential-integrity orphans (an item/wave pointing at an id the anchor docs don't define)."""
    return dev.roadmap_board(_dev_root(context_id))


@router.get("/dev/portrait", response_model=PortraitResponse)
def dev_portrait(context_id: str = "global", dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """What this project is, in six bands, one per anchor doc.

    Read-only and derived per call: the docs are the store, this is the shape the view needs."""
    return dev.read_portrait(_dev_root(context_id))


@router.get("/dev/lint", response_model=LintResponse)
def dev_lint(context_id: str = "global", dev: DevKnowledgeService = Depends(get_dev)) -> dict:
    """Health of this project's general knowledge, as findings the owner can act on.

    Derived fresh on every call, so the lint itself can never be stale."""
    return dev.lint_general(_dev_root(context_id))
