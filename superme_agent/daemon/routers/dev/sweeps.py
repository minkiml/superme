"""Standing-sweep routes — the launch bar.

Pathed under `/dev/research/` on purpose: `/dev/sweep` is already the learning capture sweep. The
item is born at `investigate`, because the button IS the classification.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from ...app_state import (
    DevKnowledgeService, DevStore, SystemSpine, get_dev, get_dev_store, get_spine,
)
from ...deps import dev_root
from ...schemas.dev.sweeps import SweepFamiliesResponse, SweepLaunchBody, SweepLaunchResponse
from ...services.runs import fire_first_investigate
from ....core import kind_profiles

log = logging.getLogger("superme-agent")

router = APIRouter()


@router.get("/dev/research/sweeps/families", response_model=SweepFamiliesResponse)
async def dev_sweep_families() -> dict:
    """The launch bar's buttons — a projection of the research-families registry.

    Read-only and repo-independent: which sweeps exist is a property of this harness. Adding a family
    is a row in that registry."""
    return {"families": [
        {"family": f.slug, "icon": f.icon, "blurb": f.blurb, "asks_interest": f.asks_interest}
        for f in kind_profiles.standing_families()
    ]}


@router.post("/dev/research/sweeps", response_model=SweepLaunchResponse)
async def dev_sweep_launch(body: SweepLaunchBody,
                           dev: DevKnowledgeService = Depends(get_dev),
                           dev_store: DevStore = Depends(get_dev_store),
                           spine: SystemSpine = Depends(get_spine)) -> dict:
    """Launch a standing sweep: mint the item at `investigate`, then fire its first run.

    An unknown family, or one that is not standing, is a 400 rather than a research item with no button
    behind it."""
    fam = kind_profiles.FAMILY_BY_SLUG.get(str(body.family))
    if fam is None:
        raise HTTPException(status_code=400, detail=f"unknown family: {body.family}")
    if not fam.standing:
        raise HTTPException(status_code=400,
                            detail=f"`{fam.slug}` is commissioned, not standing — it is raised as "
                                   f"a ticket about a named subject, not launched from a button")
    area = str(body.area or "").strip()
    interest = str(body.interest or "").strip()
    if fam.asks_interest and not interest:
        raise HTTPException(status_code=400,
                            detail=f"`{fam.slug}` needs an interest — its question is meaningless "
                                   f"until you say sound in WHAT")

    scope = area or "the whole repo"
    title = (f"{fam.slug.capitalize()} — {interest} in {scope}" if interest
             else f"{fam.slug.capitalize()} — {scope}")
    # The description IS the subject, written in the owner's frame — investigate turns it into
    # questions and walls.
    desc = "\n".join(filter(None, [
        f"Standing {fam.slug} sweep, launched from the workspace bar.",
        f"Subject: {scope}.",
        f"Interest: {interest}." if interest else "",
        "",
        fam.blurb,
    ]))
    root = dev_root(body.context_id)
    try:
        wi = dev.create_work_item(root, title=title, description=desc, kind="research",
                                  research_kind=fam.slug, born_at="investigate")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    dev_store.log_event(body.context_id, "sweep.launch",
                        f"Launched a standing {fam.slug} sweep: {scope}",
                        item_id=wi["id"], actor="owner",
                        meta={"family": fam.slug, "area": area, "interest": interest})
    started = fire_first_investigate(body.context_id, wi["id"], spine)
    return {"ok": True, "work_item": wi, "family": fam.slug, "started": started}
