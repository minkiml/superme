"""System spine routes (the Monitor/System dashboard read surface, PRD §4.11.3).

Read-only views over the System / Repo / Session / Run spine (what the system IS / is DOING / HAS
DONE) plus the model-config writes (system default + per-repo override) and the learning master switch.
"""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..app_state import SystemSpine, get_spine, dev as _dev
from ..deps import dev_root
from ..schemas.system import (
    SystemResponse, RepoOverview, RepoConnectResponse, RepoDisconnectResponse,
    RunsResponse, RunTraceResponse,
    SystemModelResponse, LearningResponse, RepoModelResponse, RepoLearningResponse,
    RepoMetaResponse, RepoAutopilotResponse, TokenUsageResponse, TokenTimeseriesResponse, SweepConfigBody, SweepConfigResponse,
    CompactionConfigBody, CompactionConfigResponse, DeputyConfigResponse,
    SystemEffortResponse, RepoEffortResponse, AgentModelsResponse, RepoAttention,
)
from ...core.models import AGENT_MODEL_FEATURES
from ...core.spine import MODES, RepoConfig
from ...core.models import CANONICAL_MODELS, is_valid_model, model_family, normalize_model

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


class DeputyConfigBody(BaseModel):
    # Either/both may be sent (partial update). enabled = deputy on/off; strictness = a PARTIAL
    # per-gate map {gate: level} — send only the gates that changed (e.g. {"review": "high"}).
    enabled: bool | None = None
    strictness: dict[str, str] | None = None  # {triage|plan|review: low·medium·high·extra}


class AgentModelBody(BaseModel):
    # Either/both may be sent. model = a TIER (`sonnet`) or concrete id; effort = low|medium|high.
    model: str | None = None
    effort: str | None = None


class RepoMetaBody(BaseModel):
    # None = leave the field unchanged; "" = clear it (back to defaults).
    color: str | None = None
    icon: str | None = None


def _norm_model(m: str | None) -> str | None:
    """Validate a model (tier alias OR concrete id) and store it as its TIER ALIAS (`sonnet`) — the
    canonical on-disk/DB form everywhere; the concrete latest is resolved only at consumption (so a
    saved pick auto-tracks a MODEL_TIERS bump instead of pinning to an old concrete id). '' / 'reset'
    / 'default' → None (clear). Raises on an unrecognized value. (models.py is the catalog.)"""
    if not m or m.strip().lower() in ("reset", "default", "inherit"):
        return None
    if not is_valid_model(m):
        raise HTTPException(status_code=400,
                            detail=f"unknown model '{m}' (use {'/'.join(CANONICAL_MODELS)})")
    return model_family(m) or normalize_model(m)


def _norm_effort(e: str | None) -> str | None:
    """Validate a reasoning-effort level; '' / 'reset' / 'default' → None (clear). Raises on unknown."""
    if not e or e in ("reset", "default", "inherit"):
        return None
    if e not in _EFFORT_LEVELS:
        raise HTTPException(status_code=400, detail=f"unknown effort '{e}' (use {'/'.join(_EFFORT_LEVELS)})")
    return e


def _active_item_count(repo_id: str) -> int:
    """Live work-items = every non-terminal item (status active or any awaiting_*; D2 runnable
    axis). Read from the work-item STORE (the item's own status is the source of truth, not run
    rows). 0 when the repo has no dev-knowledge."""
    try:
        data = _dev.read_all(dev_root(repo_id))
    except Exception:
        return 0
    return sum(1 for it in data.get("work_items", [])
               if it.get("status") in ("active", "awaiting_child", "awaiting_upstream",
                                      "awaiting_slot", "awaiting_human")
               and not it.get("done_at"))


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


class RepoConnectBody(BaseModel):
    path: str                    # absolute dir to link (created if kind=new)
    label: str | None = None     # display name (defaults to the dir name)
    kind: str                    # "new" (greenfield → project-init) | "existing" (code → retrofit)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


@router.post("/repos", response_model=RepoConnectResponse)
async def connect_repo(body: RepoConnectBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Connect a domain: register a new repo into the spine and seed its knowledge home. `kind`
    (new|existing) is stored on the repo and selects its onboarding front door (project-init |
    retrofit). New dirs are created (must be empty); existing dirs must already be a directory."""
    kind = (body.kind or "").strip()
    if kind not in ("new", "existing"):
        raise HTTPException(status_code=422, detail="kind must be 'new' or 'existing'")
    p = Path(body.path).expanduser()
    if kind == "existing":
        if not p.is_dir():
            raise HTTPException(status_code=400, detail="directory does not exist")
    else:  # new — create it, but refuse to reuse a non-empty dir
        if p.exists() and any(p.iterdir()):
            raise HTTPException(status_code=400, detail="target directory is not empty")
        p.mkdir(parents=True, exist_ok=True)
    p = p.resolve()
    label = (body.label or p.name).strip() or p.name
    existing = spine.repos()
    base = _slug(label) or _slug(p.name) or "project"
    rid, i = base, 2
    while rid in existing:                       # unique id
        rid, i = f"{base}-{i}", i + 1
    onboarding = "project-init" if kind == "new" else "retrofit"
    rc = RepoConfig(id=rid, label=label, cwd=p, layer="local", onboarding=onboarding)
    try:
        spine.add_repo(rc)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    log.info("connected repo '%s' (%s) at %s [%s]", rid, label, p, onboarding)
    return {"id": rid, "label": label, "cwd": str(p), "onboarding": onboarding}


@router.delete("/repos/{repo_id}", response_model=RepoDisconnectResponse)
async def disconnect_repo(repo_id: str, confirm: str = "",
                          spine: SystemSpine = Depends(get_spine)) -> dict:
    """Disconnect a domain — forget the project from SuperMe entirely. IRREVERSIBLE: deletes the
    registration (repos.yaml entry + kv overrides), the knowledge home, the per-repo harness cell,
    the pipeline state (inbox + learning rows) and every session (row + transcript). The project
    FOLDER itself is never touched; reconnecting later is simply a fresh connect (retrofit).
    Preserved per never-delete-logs: run/run_event/run_artifact + dev-activity events — each
    deleted session's runs are stamped session_fate='disconnected'. Guards: `?confirm=<repo id>`
    must match (the UI's typed confirmation), the hub is refused, and live runs block with 409."""
    import shutil

    rc = spine.repo(repo_id)
    if rc is None:
        raise HTTPException(status_code=404, detail="unknown repo")
    if repo_id == "global":
        raise HTTPException(status_code=400, detail="the hub cannot be disconnected")
    if confirm != repo_id:
        raise HTTPException(status_code=400, detail="confirmation mismatch: pass ?confirm=<repo id>")
    running = spine.running_run_count(repo_id)
    if running:
        raise HTTPException(status_code=409,
                            detail=f"{running} run(s) still executing — stop them first")

    from ...core.git_layer import worktrees_root
    from ...gateway import contexts
    from ..app_state import sessions as session_store, dev_store

    # 1 · sessions — hard-delete each (row + transcript) through the one deletion path; the
    # context must be resolved BEFORE the registration is removed (transcript lookup needs cwd).
    ctx = contexts.resolve(repo_id, mode="dev")
    rows = spine.sessions_for_repo(repo_id)
    for s in rows:
        session_store.delete(ctx, s["id"], cause="disconnected")

    # 2 · pipeline state (inbox + learning candidates/proposals); dev events preserved.
    pipeline_rows = dev_store.purge_context(repo_id)

    # 3 · the on-disk homes SuperMe owns. The project folder (rc.cwd) is deliberately untouched —
    # only best-effort `git worktree prune` tidies its .git metadata after the worktrees go.
    wt = worktrees_root(repo_id)
    worktrees_removed = wt.is_dir()
    shutil.rmtree(wt, ignore_errors=True)
    if worktrees_removed:
        try:
            import subprocess
            subprocess.run(["git", "worktree", "prune"], cwd=str(rc.cwd),
                           capture_output=True, text=True, timeout=15)
        except OSError as e:
            log.warning("worktree prune skipped for %s: %s", repo_id, e)
    kb = rc._knowledge_base()
    knowledge_removed = kb.is_dir()
    shutil.rmtree(kb, ignore_errors=True)
    cell = Path(rc.operational_home("dev")).parent  # local-harness/<id>/ (both scopes)
    harness_removed = cell.is_dir()
    shutil.rmtree(cell, ignore_errors=True)

    # 4 · forget the registration last, so a mid-cascade failure leaves the repo visible (retry-able).
    spine.remove_repo(repo_id)
    log.info("disconnected repo '%s' (%s): %d session(s), %d pipeline row(s)",
             repo_id, rc.label, len(rows), pipeline_rows)
    return {"id": repo_id, "label": rc.label, "sessions_deleted": len(rows),
            "pipeline_rows_deleted": pipeline_rows, "knowledge_removed": knowledge_removed,
            "harness_removed": harness_removed, "worktrees_removed": worktrees_removed}


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
            # A background learning run (forge/distill/sweep) IS a live Claude session while it runs, so
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
                "agents": active_items + lar["learn_running"] + lar["onboarding_running"],
                "running": lar["items_running"] + lar["learn_running"] + lar["onboarding_running"],
            }
        meta = spine.get_repo_meta(rc.id)
        out.append({
            "id": rc.id, "label": rc.label, "cwd": str(rc.cwd), "layer": rc.layer,
            "model_override": spine.get_model_override(rc.id),
            "effort_override": spine.get_effort_override(rc.id),
            "learning_enabled": spine.get_repo_learning(rc.id),
            "autopilot_concurrency": spine.get_autopilot_concurrency(rc.id),
            "tag_color": meta["color"], "icon": meta["icon"],
            "scopes": scopes,
        })
    return out


@router.get("/system/attention", response_model=list[RepoAttention])
async def system_attention() -> list[dict]:
    """The top-of-SuperMe attention feed (Pass 2 · Q2): every `awaiting_human` hold across ALL
    connected repos, grouped by repo and classified (escalation · breaker · paged · review · gate)
    so the notification center can badge a count and offer the right quick actions. Only repos with
    a hold appear; empty feed = nothing needs the owner."""
    from ..services import attention
    return attention.system_attention()


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


@router.get("/runs/{run_id}/trace", response_model=RunTraceResponse)
async def run_trace(run_id: int, spine: SystemSpine = Depends(get_spine)) -> dict:
    """One run's event trail — the prompt that opened it, the assistant's reply text, and each
    tool/skill/agent call, in order. Per-RUN (not per-session), so each Activity row has its own
    thread; works for background runs too. Empty list when nothing was recorded."""
    return {"run_id": run_id, "events": spine.events_for_run(run_id)}


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


@router.post("/system/deputy", response_model=DeputyConfigResponse)
async def set_system_deputy(body: DeputyConfigBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """The global deputy dial (Quick config): whether a deputy judges autopilot gates and how
    readily it escalates PER GATE (triage/plan/review, each low·medium·high·extra). Partial —
    omitted fields stay put; strictness sets only the gates it names. Rejects an unknown gate or
    level (422) rather than silently defaulting."""
    if body.enabled is not None:
        spine.set_deputy_enabled(body.enabled)
    if body.strictness is not None:
        try:
            for gate, level in body.strictness.items():
                spine.set_deputy_strictness(gate, level)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    log.info("deputy: enabled=%s strictness=%s", spine.get_deputy_enabled(),
             spine.deputy_strictness_map())
    return {"ok": True, "deputy_enabled": spine.get_deputy_enabled(),
            "deputy_strictness": spine.deputy_strictness_map()}


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


@router.get("/system/compaction", response_model=CompactionConfigResponse)
async def get_system_compaction(spine: SystemSpine = Depends(get_spine)) -> dict:
    """The compaction runtime knobs (S8/D11): trigger fill %, per-kind overrides, and the
    effectiveness threshold, plus the static incompressible floor the trigger may never sit
    at/below (what makes the knob safe to expose)."""
    from ..services.compaction import FLOOR_MIN_PCT
    return {"ok": True, **spine.get_compaction_config(), "floor_pct": FLOOR_MIN_PCT}


@router.post("/system/compaction", response_model=CompactionConfigResponse)
async def set_system_compaction(body: CompactionConfigBody,
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Tune the compaction runtime. Any omitted field is left unchanged. FLOOR-AWARE: a trigger
    the incompressible floor alone would exceed is refused (409) — never stored, never fired."""
    from ..services.compaction import FLOOR_MIN_PCT, validate_trigger
    for pct in [body.trigger_pct, *(body.by_kind or {}).values()]:
        if pct is None:
            continue
        reason = validate_trigger(int(pct))
        if reason:
            raise HTTPException(status_code=409, detail=reason)
    if (body.min_gain_pct is not None and body.min_gain_pct != "auto"
            and not (0 <= int(body.min_gain_pct) <= 95)):
        raise HTTPException(status_code=409, detail="min_gain_pct must be 0–95 or 'auto'")
    cfg = spine.set_compaction_config(trigger_pct=body.trigger_pct, by_kind=body.by_kind,
                                      min_gain_pct=body.min_gain_pct)
    log.info("compaction config: trigger=%d%% by_kind=%s min_gain=%s",
             cfg["trigger_pct"], cfg["by_kind"], cfg["min_gain_pct"])
    return {"ok": True, **cfg, "floor_pct": FLOOR_MIN_PCT}


@router.post("/repos/{repo_id}/model", response_model=RepoModelResponse)
async def set_repo_model(repo_id: str, body: RepoModelBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set (or clear) one repo's model override. Clearing falls back to the system default."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    model = _norm_model(body.model)
    spine.set_model_override(repo_id, model)
    return {"ok": True, "repo_id": repo_id, "model": model,
            "effective": normalize_model(model) or spine.effective_system_model()}


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


class AutopilotConcurrencyBody(BaseModel):
    concurrency: int


@router.post("/repos/{repo_id}/autopilot", response_model=RepoAutopilotResponse)
async def set_repo_autopilot(repo_id: str, body: AutopilotConcurrencyBody,
                             spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set this repo's autopilot concurrency cap — the max autopilot items in the build⟷vet loop at
    once (the slice-3 launch breaker). Per-project by owner decision; floored at 1."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    n = spine.set_autopilot_concurrency(repo_id, body.concurrency)
    return {"ok": True, "repo_id": repo_id, "autopilot_concurrency": n}


@router.post("/repos/{repo_id}/meta", response_model=RepoMetaResponse)
async def set_repo_meta(repo_id: str, body: RepoMetaBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set a repo's VISUAL tag: display color and/or icon (emoji). A field omitted (None) is left
    unchanged; an empty string clears it (falls back to the hashed-palette default / no icon)."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    meta = spine.set_repo_meta(repo_id, color=body.color, icon=body.icon)
    return {"ok": True, "repo_id": repo_id, "tag_color": meta["color"], "icon": meta["icon"]}
