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
    SystemModelResponse, LearningResponse, RepoModelResponse, RepoLearningResponse,
    RepoMetaResponse, TokenUsageResponse, TokenTimeseriesResponse, SweepConfigBody, SweepConfigResponse,
    SystemEffortResponse, RepoEffortResponse, AgentModelsResponse,
)
from ...core.models import AGENT_MODEL_FEATURES
from ...core.spine import MODES
from ...core.models import CANONICAL_MODELS, is_valid_model, normalize_model

router = APIRouter()
log = logging.getLogger("superme-agent")

# Model hierarchy, most-specific first: per-turn pick → per-repo override → system default → host
# default. A null/empty model clears that level.
_EFFORT_LEVELS = ("low", "medium", "high")


class SystemModelBody(BaseModel):
    model: str | None = None  # null/"" clears the system default (fall back to YAML/host)


class RepoModelBody(BaseModel):
    model: str | None = None  # null/"" clears this repo's override (fall back to system default)


class SystemEffortBody(BaseModel):
    effort: str | None = None  # null/"" clears the system default (fall back to YAML/"medium")


class RepoEffortBody(BaseModel):
    effort: str | None = None  # null/"" clears this repo's override (fall back to system default)


class LearningBody(BaseModel):
    enabled: bool


class AgentModelBody(BaseModel):
    # Either/both may be sent. model = a TIER (`sonnet`) or concrete id; effort = low|medium|high.
    model: str | None = None
    effort: str | None = None


class RepoMetaBody(BaseModel):
    # None = leave the field unchanged; "" = clear it (back to defaults).
    color: str | None = None
    icon: str | None = None


def _norm_model(m: str | None) -> str | None:
    """Validate + normalize a model (tier alias OR concrete id) to the concrete id it runs; '' /
    'reset' / 'default' → None (clear). Raises on an unrecognized value. (models.py is the catalog.)"""
    if not m or m.strip().lower() in ("reset", "default", "inherit"):
        return None
    if not is_valid_model(m):
        raise HTTPException(status_code=400,
                            detail=f"unknown model '{m}' (use {'/'.join(CANONICAL_MODELS)})")
    return normalize_model(m)


def _norm_effort(e: str | None) -> str | None:
    """Validate a reasoning-effort level; '' / 'reset' / 'default' → None (clear). Raises on unknown."""
    if not e or e in ("reset", "default", "inherit"):
        return None
    if e not in _EFFORT_LEVELS:
        raise HTTPException(status_code=400, detail=f"unknown effort '{e}' (use {'/'.join(_EFFORT_LEVELS)})")
    return e


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
    cfg = spine.get_sweep_config()
    data["sweep_idle_seconds"] = cfg["idle_seconds"]
    data["sweep_poll_seconds"] = cfg["poll_seconds"]
    data["sweep_min_user_msgs"] = cfg["min_user_msgs"]
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
        meta = spine.get_repo_meta(rc.id)
        out.append({
            "id": rc.id, "label": rc.label, "cwd": str(rc.cwd), "layer": rc.layer,
            "model_override": spine.get_model_override(rc.id),
            "effort_override": spine.get_effort_override(rc.id),
            "learning_enabled": spine.get_repo_learning(rc.id),
            "tag_color": meta["color"], "icon": meta["icon"],
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


@router.get("/tokens", response_model=TokenUsageResponse)
async def token_usage(spine: SystemSpine = Depends(get_spine)) -> dict:
    """System-wide token usage: global total + two reconciling breakdowns (semantic `by_category`
    tree + systematic `by_type`) + per-scope/per-feature splits, and the same per repo. Feeds the
    observability strip + the orbit's per-repo signal. All SuperMe-context spend."""
    return spine.token_usage()


@router.get("/tokens/timeseries", response_model=TokenTimeseriesResponse)
async def token_timeseries(tz_offset: int = 0, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Per-day token usage for the trend graph. `tz_offset` is minutes to ADD to UTC to reach the
    caller's local time (the FE sends `-getTimezoneOffset()`), so days bucket on the owner's day."""
    return spine.token_timeseries(tz_offset)


@router.post("/system/model", response_model=SystemModelResponse)
async def set_system_model(body: SystemModelBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set (or clear) the system-wide default model — the floor below per-repo overrides."""
    model = _norm_model(body.model)
    spine.set_system_model(model)
    return {"ok": True, "model": model, "effective": spine.effective_system_model()}


@router.post("/system/effort", response_model=SystemEffortResponse)
async def set_system_effort(body: SystemEffortBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set (or clear) the system-wide default reasoning effort — the floor below per-repo overrides."""
    effort = _norm_effort(body.effort)
    spine.set_system_effort(effort)
    return {"ok": True, "effort": effort, "effective": spine.effective_system_effort()}


@router.get("/system/agent-models", response_model=AgentModelsResponse)
async def get_agent_models(spine: SystemSpine = Depends(get_spine)) -> dict:
    """The tunable background agents (sweep/distill/write) with their preset, override, and effective
    model — the autonomous learning runners that pick up a model from config, not a per-turn choice."""
    return {"agents": spine.agent_model_config()}


@router.post("/system/agent-models/{feature}", response_model=AgentModelsResponse)
async def set_agent_model(feature: str, body: AgentModelBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set a background sub-agent's model TIER and/or reasoning effort — written into its own `.md`
    frontmatter (the source of truth). Send either field; both are applied when present."""
    if feature not in AGENT_MODEL_FEATURES:
        raise HTTPException(status_code=404,
                            detail=f"unknown agent '{feature}' (use {'/'.join(AGENT_MODEL_FEATURES)})")
    if body.model is not None:
        spine.set_agent_model(feature, _norm_model(body.model))
    if body.effort is not None:
        spine.set_agent_effort(feature, _norm_effort(body.effort))
    return {"agents": spine.agent_model_config()}


@router.post("/system/learning", response_model=LearningResponse)
async def set_system_learning(body: LearningBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Flip the learning master switch (idle / phase / completion sweeps). Off by default — background
    learning spends tokens unattended. Capture is fully automatic, so this governs all of it."""
    spine.set_learning_enabled(body.enabled)
    log.info("auto-learning %s", "ENABLED" if body.enabled else "disabled")
    return {"ok": True, "learning_enabled": spine.get_learning_enabled()}


@router.post("/system/sweep", response_model=SweepConfigResponse)
async def set_system_sweep(body: SweepConfigBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Tune the capture-sweep triggers: idle threshold, heartbeat cadence, and the min-new-user-message
    gate. Any omitted field is left unchanged. Takes effect without a daemon restart (the heartbeat
    reads the cadence each iteration)."""
    cfg = spine.set_sweep_config(
        idle_seconds=body.idle_seconds, poll_seconds=body.poll_seconds, min_user_msgs=body.min_user_msgs,
    )
    log.info("sweep config: idle=%ds poll=%ds min_user_msgs=%d",
             cfg["idle_seconds"], cfg["poll_seconds"], cfg["min_user_msgs"])
    return {"ok": True, **cfg}


@router.post("/repos/{repo_id}/model", response_model=RepoModelResponse)
async def set_repo_model(repo_id: str, body: RepoModelBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set (or clear) one repo's model override. Clearing falls back to the system default."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    model = _norm_model(body.model)
    spine.set_model_override(repo_id, model)
    return {"ok": True, "repo_id": repo_id, "model": model,
            "effective": model or spine.effective_system_model()}


@router.post("/repos/{repo_id}/effort", response_model=RepoEffortResponse)
async def set_repo_effort(repo_id: str, body: RepoEffortBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set (or clear) one repo's reasoning-effort override. Clearing falls back to the system default."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    effort = _norm_effort(body.effort)
    spine.set_effort_override(repo_id, effort)
    return {"ok": True, "repo_id": repo_id, "effort": effort,
            "effective": effort or spine.effective_system_effort()}


@router.post("/repos/{repo_id}/learning", response_model=RepoLearningResponse)
async def set_repo_learning(repo_id: str, body: LearningBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Opt one repo in/out of automatic capture. The global master switch still gates everything;
    this lets a single repo sit out even when the master is on."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    spine.set_repo_learning(repo_id, body.enabled)
    return {"ok": True, "repo_id": repo_id, "learning_enabled": spine.get_repo_learning(repo_id)}


@router.post("/repos/{repo_id}/meta", response_model=RepoMetaResponse)
async def set_repo_meta(repo_id: str, body: RepoMetaBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set a repo's VISUAL tag: display color and/or icon (emoji). A field omitted (None) is left
    unchanged; an empty string clears it (falls back to the hashed-palette default / no icon)."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    meta = spine.set_repo_meta(repo_id, color=body.color, icon=body.icon)
    return {"ok": True, "repo_id": repo_id, "tag_color": meta["color"], "icon": meta["icon"]}
