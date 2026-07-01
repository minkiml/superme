"""Response schemas for the health + metadata routes (health.py)."""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str


class ContextResponse(BaseModel):
    """One live context (global or a connected domain) — what the surfaces render."""
    id: str
    label: str
    layer: str
    cwd: str
