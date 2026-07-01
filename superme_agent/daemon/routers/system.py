"""System spine routes (the Monitor/System dashboard read surface, PRD §4.11.3).

Read-only views over the System / Repo / Session / Run spine (what the system IS / is DOING / HAS
DONE) plus the model-config writes (system default + per-repo override) and the learning master switch.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..app_state import SystemSpine, get_spine, dev as _dev
from ..deps import dev_root
from ..schemas.system import (
    SystemResponse, RepoOverview, RunsResponse,
    SystemModelResponse, LearningResponse, RepoModelResponse,
)
from ...core.spine import MODES

router = APIRouter()
log = logging.getLogger("superme-agent")

# Model hierarchy, most-specific first: per-turn pick → per-repo override → system default → host
# default. A null/empty model clears that level. Aliases only — keep it host-agnostic.
_MODEL_ALIASES = ("haiku", "sonnet", "opus")


class SystemModelBody(BaseModel):
    model: str | None = None  # null/"" clears the system default (fall back to YAML/host)


class RepoModelBody(BaseModel):
    model: str | None = None  # null/"" clears this repo's override (fall back to system default)


class LearningBody(BaseModel):
    enabled: bool


def _norm_model(m: str | None) -> str | None:
    """Validate a model alias; '' / 'reset' / 'default' → None (clear). Raises on unknown."""
    if not m or m in ("reset", "default", "inherit"):
        return None
    if m not in _MODEL_ALIASES:
        raise HTTPException(status_code=400, detail=f"unknown model '{m}' (use {'/'.join(_MODEL_ALIASES)})")
    return m


def _active_item_count(repo_id: str) -> int:
    """Live work-item agent jobs = items being worked — status `in_progress` or `waiting`,
    excluding queued backlog and done/dropped. Read from the work-item STORE (the item's own status
    is the source of truth, not run rows). 0 when the repo has no dev-knowledge."""
    try:
        data = _dev.read_all(dev_root(repo_id))
    except Exception:
        return 0
    return sum(1 for it in data.get("work_items", [])
               if it.get("status") in ("in_progress", "waiting") and not it.get("done_at"))


@router.get("/system", response_model=SystemResponse)
async def system_overview(spine: SystemSpine = Depends(get_spine)) -> dict:
    """The System singleton: static config + live half (in-flight runs) + the repo roster."""
    data = spine.system()
    data["repos"] = list(spine.repos().keys())
    return data


@router.get("/repos", response_model=list[RepoOverview])
async def repos_overview(spine: SystemSpine = Depends(get_spine)) -> list[dict]:
    """Every repo × scope: static meta + computed live status (active/idle, last activity,
    current item) + session/run counts + the per-scope knowledge & operational home pointers."""
    out = []
    for rc in spine.repos().values():
        scopes = {}
        # Active work-item agents come from the item STORE by status (dev scope only — core has no
        # work-items). `agents` = live items + live learning runs; `running` ⊆ agents = items
        # executing a turn now + running learning runs. Bounded + self-clearing.
        dev_active_items = _active_item_count(rc.id)
        for scope in MODES:
            st = spine.repo_status(rc.id, scope)
            lar = spine.live_agent_runs(rc.id, scope)
            active_items = dev_active_items if scope == "dev" else 0
            # A headless learning run (forge/distill/sweep) IS a live Claude session while it runs, so
            # count it in `sessions` too — but it's disposable (sessionless, transcript discarded on
            # finish), so it leaves NO row to clean: the moment it finishes it drops out of
            # learn_running and the count self-corrects. So sessions never goes stale.
            scopes[scope] = {
                "knowledge_home": str(rc.knowledge_home(scope)),
                "operational_home": str(rc.operational_home(scope)),
                "active": st["active"],
                "current_item": st["current_item"],
                "last_activity": st["last_activity"],
                "sessions": spine.session_count(rc.id, scope) + lar["learn_running"],
                "agents": active_items + lar["learn_running"],
                "running": lar["items_running"] + lar["learn_running"],
            }
        out.append({
            "id": rc.id, "label": rc.label, "cwd": str(rc.cwd), "layer": rc.layer,
            "model_override": spine.get_model_override(rc.id),
            "scopes": scopes,
        })
    return out


@router.get("/runs", response_model=RunsResponse)
async def runs_overview(context_id: str | None = None, limit: int = 50,
                        spine: SystemSpine = Depends(get_spine)) -> dict:
    """The run log: live (in-flight) + recent history. Scope to one repo with ?context_id=,
    or omit for the system-wide log. `running` is the live count for a quick gauge."""
    if context_id:
        live = spine.live_runs(context_id)
        history = spine.run_history(context_id, limit=limit)
    else:
        live = spine.live_runs()
        history = spine.recent_runs(limit=limit)
    return {"live": live, "history": history, "running": len(live)}


@router.post("/system/model", response_model=SystemModelResponse)
async def set_system_model(body: SystemModelBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set (or clear) the system-wide default model — the floor below per-repo overrides."""
    model = _norm_model(body.model)
    spine.set_system_model(model)
    return {"ok": True, "model": model, "effective": spine.effective_system_model()}


@router.post("/system/learning", response_model=LearningResponse)
async def set_system_learning(body: LearningBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Flip the learning master switch (idle / phase / completion sweeps). Off by default — background
    learning spends tokens unattended. Capture is fully automatic, so this governs all of it."""
    spine.set_learning_enabled(body.enabled)
    log.info("auto-learning %s", "ENABLED" if body.enabled else "disabled")
    return {"ok": True, "learning_enabled": spine.get_learning_enabled()}


@router.post("/repos/{repo_id}/model", response_model=RepoModelResponse)
async def set_repo_model(repo_id: str, body: RepoModelBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set (or clear) one repo's model override. Clearing falls back to the system default."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    model = _norm_model(body.model)
    spine.set_model_override(repo_id, model)
    return {"ok": True, "repo_id": repo_id, "model": model,
            "effective": model or spine.effective_system_model()}
