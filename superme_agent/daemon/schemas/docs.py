"""Response schemas for the Docs dashboard routes (docs.py)."""

from pydantic import BaseModel


class DocSummary(BaseModel):
    """A doc's index entry (slug + title) for the list view."""
    slug: str
    title: str


class DocsListResponse(BaseModel):
    docs: list[DocSummary]


class DocResponse(BaseModel):
    """One doc's full markdown."""
    slug: str
    title: str
    content: str
