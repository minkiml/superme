"""System spine routes — the Monitor dashboard's read surface.

Read-only views over the System, Repo, Session and Run spine, plus the model-config writes and the
learning master switch.
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
    LearningResponse, RepoModelResponse, RepoLearningResponse,
    RepoMetaResponse, RepoGitResponse, RepoBranchesResponse, RepoAutopilotResponse, TokenUsageResponse, TokenTimeseriesResponse, SweepConfigBody, SweepConfigResponse,
    CompactionConfigBody, CompactionConfigResponse, DeputyConfigResponse,
    RepoEffortResponse, AgentModelsResponse, RepoAttention,
)
from ...core.models import AGENT_MODEL_FEATURES
from ...core import git_layer
from ...core.spine import MODES, RepoConfig
from ...core.models import CANONICAL_MODELS, is_valid_model, model_family, normalize_model

router = APIRouter()
log = logging.getLogger("superme-agent")

# Model hierarchy, most-specific first: turn → repo → system → host. A null model clears that
# level.
_EFFORT_LEVELS = ("low", "medium", "high")


class RepoModelBody(BaseModel):
    model: str | None = None  # null/"" clears this repo's override (fall back to the default)
    # Which role's tier this sets; omitted means the project's own. `vet` resolves on its own
    # chain.
    role: str = "default"


class RepoEffortBody(BaseModel):
    effort: str | None = None  # null/"" clears this repo's override (fall back to the default)
    role: str = "default"


class LearningBody(BaseModel):
    enabled: bool


class DeputyConfigBody(BaseModel):
    # Partial update: `strictness` is a per-gate map, so send only the gates that changed.
    enabled: bool | None = None
    strictness: dict[str, str] | None = None  # {triage|plan|review: low·medium·high·extra}
    # One judge across every project, so one answer, set here rather than per repo. Never the
    # project's tier.
    model: str | None = None
    effort: str | None = None


class AgentModelBody(BaseModel):
    # Either/both may be sent. model = a TIER (`sonnet`) or concrete id; effort = low|medium|high.
    model: str | None = None
    effort: str | None = None


class RepoMetaBody(BaseModel):
    # None = leave the field unchanged; "" = clear it (back to defaults).
    color: str | None = None
    icon: str | None = None


def _norm_model(m: str | None) -> str | None:
    """Validate a model — tier alias or concrete id — and store it as its TIER ALIAS.

    The concrete latest is resolved at consumption, so a saved pick tracks a tier bump instead of
    pinning an old id."""
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
    """Live work-items: every non-terminal item, read from the work-item store rather than run rows.
    0 when the repo has no dev-knowledge."""
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
    """Connect a domain: register a new repo into the spine and seed its knowledge home.

    `kind` selects its onboarding front door. New dirs are created and must be empty; existing dirs
    must already be a directory."""
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
    # Seed the mandate boilerplate so a fresh repo has a standing bar to judge against. Never fail
    # connect.
    try:
        from ...core import deputy as deputy_core
        deputy_core.read_mandate(deputy_core.deputy_root(rid))  # seed=True writes the template
    except Exception:
        log.warning("deputy mandate seed skipped for '%s'", rid, exc_info=True)
    log.info("connected repo '%s' (%s) at %s [%s]", rid, label, p, onboarding)
    return {"id": rid, "label": label, "cwd": str(p), "onboarding": onboarding}


@router.delete("/repos/{repo_id}", response_model=RepoDisconnectResponse)
async def disconnect_repo(repo_id: str, confirm: str = "",
                          spine: SystemSpine = Depends(get_spine)) -> dict:
    """Disconnect a domain — forget the project from SuperMe entirely. IRREVERSIBLE.

    Deletes the registration, knowledge home, harness cell, pipeline state and every session. The
    project FOLDER is never touched. Run rows and dev events are preserved and stamped
    `session_fate='disconnected'`."""
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

    # Resolve the context BEFORE removing the registration: the transcript lookup needs the cwd.
    ctx = contexts.resolve(repo_id, mode="dev")
    rows = spine.sessions_for_repo(repo_id)
    for s in rows:
        session_store.delete(ctx, s["id"], cause="disconnected")

    # 2 · pipeline state (inbox + learning candidates/proposals); dev events preserved.
    pipeline_rows = dev_store.purge_context(repo_id)

    # The project folder is deliberately untouched; only `git worktree prune` tidies its .git
    # metadata.
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
    """Every repo and scope: static meta, computed live status, session and run counts, and the per-scope
    knowledge and operational home pointers."""
    out = []
    for rc in spine.repos().values():
        scopes = {}
        # `agents` is live items plus live learning runs; `running` is the subset executing a turn
        # now.
        dev_active_items = _active_item_count(rc.id)
        for scope in MODES:
            st = spine.repo_status(rc.id, scope)
            lar = spine.live_agent_runs(rc.id, scope)
            active_items = dev_active_items if scope == "dev" else 0
            # A learning run is a live session while it runs, but disposable: it leaves no row to
            # clean.
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
            # Unset means the floor, not the lines above: vet does not inherit the tier of the
            # thing it checks.
            "vet_model": spine.get_model_override(rc.id, "vet"),
            "vet_effort": spine.get_effort_override(rc.id, "vet"),
            "learning_enabled": spine.get_repo_learning(rc.id),
            "autopilot_concurrency": spine.get_autopilot_concurrency(rc.id),
            "tag_color": meta["color"], "icon": meta["icon"],
            "review_mode": rc.review_mode, "anchor_branch": rc.anchor_branch,
            **_anchor_state(rc),
            "scopes": scopes,
        })
    return out


@router.get("/system/attention", response_model=list[RepoAttention])
async def system_attention() -> list[dict]:
    """The top-of-SuperMe attention feed: every `awaiting_human` hold across all connected repos, grouped
    by repo and classified.

    Only repos with a hold appear; an empty feed means nothing needs the owner."""
    from ..services import attention
    return attention.system_attention()


@router.get("/runs", response_model=RunsResponse)
async def runs_overview(context_id: str | None = None, limit: int = 50,
                        spine: SystemSpine = Depends(get_spine)) -> dict:
    """The run log: live plus recent history. Scope to one repo with `context_id`, or omit for the
    system-wide log."""
    if context_id:
        live = spine.live_runs(context_id)
        history = spine.run_history(context_id, limit=limit)
    else:
        live = spine.live_runs()
        history = spine.recent_runs(limit=limit)
    return {"live": live, "history": history, "running": len(live)}


@router.get("/runs/{run_id}/trace", response_model=RunTraceResponse)
async def run_trace(run_id: int, spine: SystemSpine = Depends(get_spine)) -> dict:
    """One run's event trail — the opening prompt, the assistant's reply, and each call, in order.

    Per-RUN rather than per-session, so each Activity row has its own thread."""
    return {"run_id": run_id, "events": spine.events_for_run(run_id)}


@router.get("/tokens", response_model=TokenUsageResponse)
async def token_usage(spine: SystemSpine = Depends(get_spine)) -> dict:
    """System-wide token usage: the global total, two reconciling breakdowns, and per-scope and
    per-feature splits, the same per repo."""
    return spine.token_usage()


@router.get("/tokens/timeseries", response_model=TokenTimeseriesResponse)
async def token_timeseries(tz_offset: int = 0, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Per-day token usage for the trend graph. `tz_offset` is minutes to ADD to UTC to reach the
    caller's local time, so days bucket on the owner's day."""
    return spine.token_timeseries(tz_offset)


@router.get("/system/agent-models", response_model=AgentModelsResponse)
async def get_agent_models(spine: SystemSpine = Depends(get_spine)) -> dict:
    """The tunable background agents with their preset, override and effective model — the autonomous
    runners that take a model from config rather than a per-turn choice."""
    return {"agents": spine.agent_model_config()}


@router.post("/system/agent-models/{feature}", response_model=AgentModelsResponse)
async def set_agent_model(feature: str, body: AgentModelBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set a background sub-agent's model tier and/or reasoning effort, written into its own `.md`
    frontmatter. Send either field; both apply when present."""
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
    """Flip the learning master switch. Off by default, because background learning spends tokens
    unattended."""
    spine.set_learning_enabled(body.enabled)
    log.info("auto-learning %s", "ENABLED" if body.enabled else "disabled")
    return {"ok": True, "learning_enabled": spine.get_learning_enabled()}


@router.post("/system/deputy", response_model=DeputyConfigResponse)
async def set_system_deputy(body: DeputyConfigBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """The global deputy dial: whether a deputy judges autopilot gates, and how readily it escalates PER
    GATE.

    Partial — omitted fields stay put. Rejects an unknown gate or level rather than silently
    defaulting."""
    if body.enabled is not None:
        spine.set_deputy_enabled(body.enabled)
    if body.strictness is not None:
        try:
            for gate, level in body.strictness.items():
                spine.set_deputy_strictness(gate, level)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    if body.model is not None:
        spine.set_deputy_model(_norm_model(body.model))
    if body.effort is not None:
        spine.set_deputy_effort(_norm_effort(body.effort))
    log.info("deputy: enabled=%s strictness=%s tier=%s/%s", spine.get_deputy_enabled(),
             spine.deputy_strictness_map(), spine.get_deputy_model(), spine.get_deputy_effort())
    d_model, d_effort = spine.deputy_params()
    return {"ok": True, "deputy_enabled": spine.get_deputy_enabled(),
            "deputy_strictness": spine.deputy_strictness_map(),
            "deputy_model": spine.get_deputy_model(), "deputy_effort": spine.get_deputy_effort(),
            "deputy_effective_model": d_model, "deputy_effective_effort": d_effort}


@router.post("/system/sweep", response_model=SweepConfigResponse)
async def set_system_sweep(body: SweepConfigBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Tune the capture-sweep triggers. Any omitted field is left unchanged, and a change takes effect
    without a restart."""
    cfg = spine.set_sweep_config(
        idle_seconds=body.idle_seconds, poll_seconds=body.poll_seconds, min_user_msgs=body.min_user_msgs,
    )
    log.info("sweep config: idle=%ds poll=%ds min_user_msgs=%d",
             cfg["idle_seconds"], cfg["poll_seconds"], cfg["min_user_msgs"])
    return {"ok": True, **cfg}


@router.get("/system/compaction", response_model=CompactionConfigResponse)
async def get_system_compaction(spine: SystemSpine = Depends(get_spine)) -> dict:
    """The compaction runtime knobs, plus the static incompressible floor the trigger may never sit at
    or below — what makes the knob safe to expose."""
    from ..services.compaction import FLOOR_MIN_PCT, TRIGGER_MIN_PCT
    return {"ok": True, **spine.get_compaction_config(),
            "floor_pct": FLOOR_MIN_PCT, "min_pct": TRIGGER_MIN_PCT}


@router.post("/system/compaction", response_model=CompactionConfigResponse)
async def set_system_compaction(body: CompactionConfigBody,
                                spine: SystemSpine = Depends(get_spine)) -> dict:
    """Tune the compaction runtime. Any omitted field is left unchanged.

    FLOOR-AWARE: a trigger the incompressible floor alone would exceed is refused, never stored."""
    from ..services.compaction import FLOOR_MIN_PCT, TRIGGER_MIN_PCT, validate_trigger
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
    return {"ok": True, **cfg, "floor_pct": FLOOR_MIN_PCT, "min_pct": TRIGGER_MIN_PCT}


@router.post("/repos/{repo_id}/model", response_model=RepoModelResponse)
async def set_repo_model(repo_id: str, body: RepoModelBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set (or clear) one repo's model override. Clearing falls back to the system default."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    if body.role not in spine.ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {sorted(spine.ROLES)}")
    model = _norm_model(body.model)
    spine.set_model_override(repo_id, model, body.role)
    return {"ok": True, "repo_id": repo_id, "role": body.role, "model": model,
            "effective": normalize_model(model) or spine.effective_system_model()}


@router.post("/repos/{repo_id}/effort", response_model=RepoEffortResponse)
async def set_repo_effort(repo_id: str, body: RepoEffortBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set (or clear) one repo's reasoning-effort override. Clearing falls back to the system default."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    if body.role not in spine.ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {sorted(spine.ROLES)}")
    effort = _norm_effort(body.effort)
    spine.set_effort_override(repo_id, effort, body.role)
    return {"ok": True, "repo_id": repo_id, "role": body.role, "effort": effort,
            "effective": effort or spine.effective_system_effort()}


@router.post("/repos/{repo_id}/learning", response_model=RepoLearningResponse)
async def set_repo_learning(repo_id: str, body: LearningBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Opt one repo in or out of automatic capture. The global master switch still gates everything."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    spine.set_repo_learning(repo_id, body.enabled)
    return {"ok": True, "repo_id": repo_id, "learning_enabled": spine.get_repo_learning(repo_id)}


class AutopilotConcurrencyBody(BaseModel):
    concurrency: int


@router.post("/repos/{repo_id}/autopilot", response_model=RepoAutopilotResponse)
async def set_repo_autopilot(repo_id: str, body: AutopilotConcurrencyBody,
                             spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set this repo's autopilot concurrency cap — the most autopilot items in the build⟷vet loop at
    once. Floored at 1."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    n = spine.set_autopilot_concurrency(repo_id, body.concurrency)
    return {"ok": True, "repo_id": repo_id, "autopilot_concurrency": n}


class RepoGitBody(BaseModel):
    # None = leave unchanged; "" clears anchor_branch back to "derive the repo's default branch".
    review_mode: str | None = None
    anchor_branch: str | None = None


def _anchor_state(rc) -> dict:
    """What this repo's anchor resolves to right now, or why it does not.

    Read at request time, because a branch can be deleted between two settings loads and the owner
    should see that here rather than at a merge."""
    try:
        if not git_layer.is_git_repo(rc.cwd):
            return {"resolved_anchor": None, "anchor_error": None}
        return {"resolved_anchor": git_layer.resolve_anchor(rc.cwd, rc.anchor_branch),
                "anchor_error": None}
    except git_layer.GitError as e:
        return {"resolved_anchor": None, "anchor_error": str(e)}


@router.post("/repos/{repo_id}/git", response_model=RepoGitResponse)
async def set_repo_git(repo_id: str, body: RepoGitBody,
                       spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set this repo's review mode and/or anchor branch.

    Both are read LIVE at every decision point. An anchor naming a branch that does not exist is
    accepted and reported back as an `error`."""
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        rc = spine.update_repo(repo_id, **patch) if patch else spine.repo(repo_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if rc is None:
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    return {"ok": True, "repo_id": repo_id, "review_mode": rc.review_mode,
            "anchor_branch": rc.anchor_branch, **_anchor_state(rc)}


@router.get("/repos/{repo_id}/branches", response_model=RepoBranchesResponse)
async def get_repo_branches(repo_id: str, spine: SystemSpine = Depends(get_spine)) -> dict:
    """This repo's local branches — the anchor picker's option set.

    Read live off git: the anchor REFUSES on a branch that does not exist, so a stale list would offer
    a setting that fails at the next merge."""
    rc = spine.repo(repo_id)
    if rc is None:
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    if not git_layer.is_git_repo(rc.cwd):
        return {"repo_id": repo_id, "branches": [], "anchor": None, "anchor_error": None}
    st = _anchor_state(rc)
    return {"repo_id": repo_id, "branches": git_layer.list_branches(rc.cwd),
            "anchor": st["resolved_anchor"], "anchor_error": st["anchor_error"]}


@router.post("/repos/{repo_id}/meta", response_model=RepoMetaResponse)
async def set_repo_meta(repo_id: str, body: RepoMetaBody, spine: SystemSpine = Depends(get_spine)) -> dict:
    """Set a repo's VISUAL tag: display color and/or icon (emoji). A field omitted (None) is left
    unchanged; an empty string clears it (falls back to the hashed-palette default / no icon)."""
    if repo_id not in spine.repos():
        raise HTTPException(status_code=404, detail=f"unknown repo '{repo_id}'")
    meta = spine.set_repo_meta(repo_id, color=body.color, icon=body.icon)
    return {"ok": True, "repo_id": repo_id, "tag_color": meta["color"], "icon": meta["icon"]}
