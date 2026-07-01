"""Response schemas for the system spine routes (system.py).

The Monitor/System dashboard read surface: the System singleton, the repo roster, and the run log,
plus the model-config + learning-switch write results.
"""

from pydantic import BaseModel

from .common import RunMode, RunStatus


class RunRow(BaseModel):
    """One row of the spine `run` table (live or historical) — every run-returning route shares it.
    mode/status are locked (R5: the spine writes exactly these); `feature` stays a free label."""
    id: int
    repo_id: str
    mode: RunMode
    feature: str
    session_id: str | None = None
    item_id: str | None = None
    status: RunStatus
    model: str | None = None
    tokens: int
    ctx_pct: int | None = None
    started_at: str
    ended_at: str | None = None


class SystemResponse(BaseModel):
    """The System singleton: static config + the live half (in-flight runs) + the repo roster."""
    identity: str
    version: int
    default_model: str | None = None
    default_model_static: str | None = None
    default_model_overridden: bool
    policy_version: int
    default_repo: str
    learning_enabled: bool
    live_runs: list[RunRow]
    running: int
    repos: list[str]


class RepoScope(BaseModel):
    """One (repo × scope) cell: home pointers + computed live status + counts."""
    knowledge_home: str
    operational_home: str
    active: bool
    current_item: str | None = None
    last_activity: str | None = None
    sessions: int
    agents: int
    running: int


class RepoOverview(BaseModel):
    id: str
    label: str
    cwd: str
    layer: str
    model_override: str | None = None
    scopes: dict[str, RepoScope]


class RunsResponse(BaseModel):
    live: list[RunRow]
    history: list[RunRow]
    running: int


class SystemModelResponse(BaseModel):
    ok: bool
    model: str | None = None
    effective: str | None = None


class LearningResponse(BaseModel):
    ok: bool
    learning_enabled: bool


class RepoModelResponse(BaseModel):
    ok: bool
    repo_id: str
    model: str | None = None
    effective: str | None = None
