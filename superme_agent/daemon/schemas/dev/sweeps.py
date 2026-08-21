"""Schemas for the standing-sweep launch bar (routers/dev/sweeps.py)."""

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
    # False is not a failure: the sweep exists and rests at investigate for a chat-driven pass.
    started: bool


class SweepLaunchBody(BaseModel):
    family: str
    context_id: str = "global"
    # Empty means the whole repo, which is the honest default: the record has to say which breadth
    # it got.
    area: str = ""
    # No typed field for interest: one that only leans is unreliable, so it rides the description.
    interest: str = ""
