"""Health + metadata routes: /health, /contexts."""

from fastapi import APIRouter

from ...gateway import contexts
from ..schemas.health import HealthResponse, ContextResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> dict:
    return {"status": "ok", "service": "superme-core-daemon"}


@router.get("/contexts", response_model=list[ContextResponse])
async def contexts_list() -> list[dict]:
    """Live contexts (global + connected domains) for the surfaces to render."""
    return contexts.list_all()
