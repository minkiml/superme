"""Response schemas for the standing-sweep launch bar (routers/dev/sweeps.py)."""

from pydantic import BaseModel

from .work_items import WorkItem


class SweepFamily(BaseModel):
    """One launch button — a projection of `kind_profiles.ResearchFamily`. Only STANDING families
    appear: a commissioned one is raised as a ticket about a named subject, so it has no button."""
    family: str
    icon: str            # lucide-react icon name; the bar maps it
    blurb: str           # one line, shown on the button's tooltip
    asks_interest: bool  # audit only — its question is meaningless until an interest is named


class SweepFamiliesResponse(BaseModel):
    families: list[SweepFamily] = []


class SweepLaunchResponse(BaseModel):
    ok: bool
    work_item: WorkItem
    family: str
    # Did the first investigate run actually start? False is not a failure — the sweep exists and
    # rests at investigate for a chat-driven pass. The surface says which, rather than implying a
    # run that never began.
    started: bool
