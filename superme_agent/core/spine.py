"""SystemSpine — the authoritative System / Repo / Session / Run data model (PRD §4.11.3).

The keystone of the renovation: the first-class model of the system that didn't exist
before. It absorbs state previously smeared across `registry.yaml`, `.sessions.json`,
`.context_models.json`, the `.dev.db:runs` table, and the daemon's in-memory
`_planning`/`_distilling` dicts into one coherent, queryable spine — both the data behind
the monitor dashboard AND (later) the agent's self-model.

Substrate split (decision 2):
  • STATIC-meta  — hand-editable, git-tracked YAML (`config/system.yaml`, `config/repos.yaml`),
                   LOADED + validated here. The repo HOME paths are derived by convention
                   (the WI-3 relocation pass edits the convention, not every entry).
  • LIVE-status  — a SQLite DB (`.system.db`) this module OWNS: `session`, `run`,
                   `model_override`. Repo live-status (active/idle, last activity, current
                   item) is COMPUTED from runs, not stored.

Four entities, not three (decision 3): Session = a durable, resumable container (chat
threads + resumable work); Run = an execution (a turn, or a standalone workflow pass)
carrying telemetry + live status. One Session has many Runs. A workflow pass (distill) is
a standalone Run with `session_id=NULL` — so it *structurally* cannot reach the resumable
picker (which lists Sessions). That dissolves the old distill defects rather than guarding
against them.

Lattice note: in this codebase the {core, dev} SCOPE axis ≡ `mode`. So Session/Run key on
(repo_id × mode), and the distill run-guard keys on (repo_id, mode, feature).

Localhost, single-owner: short-lived connections per call are plenty (mirrors DevStore).
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..runtime.config import (
    KNOWLEDGE_REPO_DIR,
    LOCAL_HARNESS_DIR,
    REPOS_CONFIG_FILE,
    ROOT_DIR,
    SYSTEM_CONFIG_FILE,
    SYSTEM_DB_FILE,
)
from .kind_profiles import AGENT_THREAD_KINDS

log = logging.getLogger("superme-agent")

# The {core, dev} scope lattice axis (≡ Context.mode in this codebase).
MODES = ("core", "dev")
# SESSION-DISPOSABLE features: standalone, sessionless workflow passes (distill, …) whose SDK
# transcript is THROWAWAY — no resumable session is needed after the job, so the caller deletes
# the transcript on finish. The Run ROW is always KEPT regardless (it is the durable run log /
# telemetry); only the on-disk conversation transcript is disposed. Durable features (chat, plan)
# keep their transcript (resumable). Transcript deletion is a daemon concern (it holds the
# Context + SessionStore); the spine just exposes the set.
DISPOSABLE_FEATURES = {"distill", "sweep", "write"}
# Run features that are explicit LEARNING agent jobs (sessionless; counted as "agents" only while
# running). Work-item jobs are counted separately (via their item-bound runs). See agent_counts().
LEARNING_FEATURES = {"distill", "sweep", "capture", "write"}
_RUN_STATUSES = {"running", "done", "aborted", "waiting"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(p) -> str:
    """Canonical absolute path string, so equal cwds compare equal."""
    return str(Path(p).resolve())


def _duration_ms(started_at: str | None, ended_at: str | None) -> int | None:
    """Run wall-clock from the ISO started/ended stamps (None if either is missing/unparseable)."""
    if not started_at or not ended_at:
        return None
    try:
        return int((datetime.fromisoformat(ended_at)
                    - datetime.fromisoformat(started_at)).total_seconds() * 1000)
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- config


@dataclass
class SystemConfig:
    """The System singleton's static-meta (config/system.yaml)."""
    version: int = 1
    identity: str = "SuperMe"
    default_model: str | None = None
    default_effort: str | None = None  # reasoning effort floor (low|medium|high); None → "medium"
    policy_version: int = 1
    default_repo: str = "global"


REVIEW_MODES = ("fast", "strict")
REVIEW_MODE_DEFAULT = "fast"


@dataclass
class RepoConfig:
    """One repo's static-meta (an entry in config/repos.yaml). Home paths are derived by
    convention (knowledge_home/operational_home), not stored."""
    id: str
    label: str
    cwd: Path
    layer: str = "local"          # "global" | "local"
    persona_append: str = ""
    extra_mcp: list = field(default_factory=list)
    onboarding: str | None = None  # "project-init" | "retrofit" — the connect-time choice that
    # selects the onboarding front door until memory is established; None = let the owner pick.
    review_mode: str = REVIEW_MODE_DEFAULT  # "fast" | "strict" — governs exactly one thing: whether
    # the diff gets its own review gate before it lands (workflow-renovation-v2 §2.2). `fast` =
    # approving the item merges it; `strict` = deputy approve opens the PR, the owner's approve on
    # the PR page merges. Named `review_mode`, not `mode`: `Context.mode` already means core|dev.
    anchor_branch: str | None = None  # the branch every git site targets (branch-from base · sync
    # source · merge target). None = derive the repo's own default branch; set it when direct-to-main
    # is forbidden and a sanctioned intermediate (e.g. `develop`) is the furthest SuperMe may go.
    vet_env: dict | None = None  # how to boot a server that runs an ITEM WORKTREE's code, so a
    # check that queries one is not answered by whatever instance happens to be listening (which
    # serves a different checkout — a deleted endpoint still reads as present there). Keys:
    # `cmd` (required; `{port}` is substituted), `port_env` (env var carrying the port, when the
    # command reads one instead), `ready` (path that answers 200 once up), `url_env` (the variable
    # the checks read). None = this repo has no vet env, and a check needing one is UNRUNNABLE here
    # rather than silently retargeted. Read by the vet skill's scripts/vet_env.sh, which owns the
    # lifecycle; this owns only what to start.

    def __post_init__(self):
        if not self.label:
            self.label = self.id
        self.cwd = Path(self.cwd)
        if self.review_mode not in REVIEW_MODES:
            log.warning("repo %r: unknown review_mode %r; using %r",
                        self.id, self.review_mode, REVIEW_MODE_DEFAULT)
            self.review_mode = REVIEW_MODE_DEFAULT
        self.anchor_branch = (self.anchor_branch or "").strip() or None
        # A block without a `cmd` names nothing to start — treat it as absent rather than let a
        # half-filled entry read as "this repo has a vet env" at the one moment that matters.
        if isinstance(self.vet_env, dict) and not str(self.vet_env.get("cmd") or "").strip():
            log.warning("repo %r: vet_env has no `cmd`; ignoring it", self.id)
            self.vet_env = None
        elif self.vet_env is not None and not isinstance(self.vet_env, dict):
            log.warning("repo %r: vet_env must be a mapping; ignoring it", self.id)
            self.vet_env = None

    # --- home conventions (the relocation pass edits THESE, not the YAML) -----------
    def _knowledge_base(self) -> Path:
        """This repo's knowledge sub-home in the central knowledge repo (renovation §4.11.2):
        superme-knowledge/<id>-knowledge/ — for global AND local repos alike."""
        return KNOWLEDGE_REPO_DIR / f"{self.id}-knowledge"

    def knowledge_home(self, scope: str) -> Path:
        """The knowledge home for a scope (renovation §4.11.2):
        core → <base>/core; dev → <base>/dev (the `internal/` nesting is dropped)."""
        return self._knowledge_base() / scope

    def operational_home(self, scope: str) -> Path:
        """The per-repo operational home for a scope (additive over the universal harness),
        under the CODE tree (renovation §4.11.1): superme_agent/local-harness/<id>/<scope>.
        This dir IS the per-repo plugin root (skills/agents load when it carries a manifest)."""
        return LOCAL_HARNESS_DIR / self.id / scope

    def constitution_home(self, scope: str) -> Path:
        """The per-repo learned-constitution home (WI-8): one file per item, assembled into the
        system prompt for this repo × scope. Sits inside the per-repo operational cell."""
        return self.operational_home(scope) / "constitution"

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "label": self.label,
            "cwd": str(self.cwd),
            "layer": self.layer,
            "persona_append": self.persona_append,
            "extra_mcp": list(self.extra_mcp),
        }
        if self.onboarding:
            d["onboarding"] = self.onboarding
        # Only non-defaults are written: an entry with neither key reads as `fast` + derived anchor,
        # which is what an untouched repo means. Keeps existing entries byte-identical.
        if self.review_mode != REVIEW_MODE_DEFAULT:
            d["review_mode"] = self.review_mode
        if self.anchor_branch:
            d["anchor_branch"] = self.anchor_branch
        if self.vet_env:
            d["vet_env"] = dict(self.vet_env)
        return d


def load_system_config(path: Path = SYSTEM_CONFIG_FILE) -> SystemConfig:
    """Load config/system.yaml → SystemConfig (defaults if the file is absent/empty)."""
    try:
        raw = yaml.safe_load(Path(path).read_text()) or {}
    except (FileNotFoundError, yaml.YAMLError) as e:
        log.warning("system.yaml unavailable (%s); using defaults.", e)
        raw = {}
    return SystemConfig(
        version=int(raw.get("version", 1)),
        identity=raw.get("identity", "SuperMe"),
        default_model=raw.get("default_model"),
        default_effort=raw.get("default_effort"),
        policy_version=int(raw.get("policy_version", 1)),
        default_repo=raw.get("default_repo", "global"),
    )


def load_repos(path: Path = REPOS_CONFIG_FILE) -> dict[str, RepoConfig]:
    """Load config/repos.yaml → {id: RepoConfig}. Relative cwds resolve against ROOT_DIR
    (the global repo's "." → the repo root). Empty/absent file → {} (graceful)."""
    try:
        raw = yaml.safe_load(Path(path).read_text()) or {}
    except (FileNotFoundError, yaml.YAMLError) as e:
        log.warning("repos.yaml unavailable (%s); no repos loaded.", e)
        return {}
    out: dict[str, RepoConfig] = {}
    for rid, spec in (raw.get("repos") or {}).items():
        spec = spec or {}
        cwd = Path(spec.get("cwd", "."))
        if not cwd.is_absolute():
            cwd = (ROOT_DIR / cwd).resolve()
        out[rid] = RepoConfig(
            id=rid,
            label=spec.get("label") or rid,
            cwd=cwd,
            layer=spec.get("layer") or ("global" if rid == "global" else "local"),
            persona_append=(spec.get("persona_append") or "").strip(),
            extra_mcp=spec.get("extra_mcp") or [],
            onboarding=spec.get("onboarding") or None,
            review_mode=(spec.get("review_mode") or REVIEW_MODE_DEFAULT),
            anchor_branch=spec.get("anchor_branch") or None,
            vet_env=spec.get("vet_env") or None,
        )
    return out


# ----------------------------------------------------------------------------- store


class SystemSpine:
    """The authoritative spine: loads static config + owns the live `.system.db`."""

    def __init__(self, db_path: Path = SYSTEM_DB_FILE,
                 system_config: Path = SYSTEM_CONFIG_FILE,
                 repos_config: Path = REPOS_CONFIG_FILE):
        self.db_path = Path(db_path)
        self._system_config_path = Path(system_config)
        self._repos_config_path = Path(repos_config)
        self._init_db()

    # --- connection / schema ----------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _ensure_columns(c: sqlite3.Connection, table: str, cols: dict[str, str]) -> None:
        """Additive, idempotent migration: ALTER-add any of `cols` (name → SQL type/decl) that a
        pre-existing table lacks. Safe on every startup — new DBs already have them from CREATE,
        old DBs get them once. SQLite requires a constant/omitted DEFAULT for ADD COLUMN, which our
        `DEFAULT 0`/nullable decls satisfy, so existing rows backfill to 0/NULL without a rewrite."""
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols.items():
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    # --- token-usage accounting helpers (token-usage-tracking-spec) --------------
    @staticmethod
    def _usage_parts(usage: dict | None) -> tuple[int, int, int, int]:
        """Split a raw SDK usage dict into (input, cache_creation, cache_read, output) ints."""
        u = usage or {}
        return (
            int(u.get("input_tokens", 0) or 0),
            int(u.get("cache_creation_input_tokens", 0) or 0),
            int(u.get("cache_read_input_tokens", 0) or 0),
            int(u.get("output_tokens", 0) or 0),
        )

    @staticmethod
    def _legacy_tokens(i: int, cc: int, cr: int, o: int) -> int:
        """The back-compat `tokens` scalar: input + output + cache_creation (EXCLUDES cache_read),
        matching agent_service._sum_tokens so existing telemetry/guard readers are unchanged."""
        return i + o + cc

    @staticmethod
    def _display_tokens(row) -> int:
        """The reconciling per-run token amount for DISPLAY (activity log, run cards): the THREE main
        typed columns — input + cache_creation + output — EXCLUDING cache_read (cheap re-reads of
        already-cached context, which otherwise dwarf the number ~1000× and drown the real "new work"
        signal). Falls back to the legacy scalar for pre-migration rows (already a 3-type sum). This is
        the SAME 3-type definition the token dashboard's default (`total`) sums, so a run's number
        reconciles across surfaces; the full 4-type volume lives behind the dashboard's toggle."""
        typed = ((row["tok_input"] or 0) + (row["tok_cache_creation"] or 0)
                 + (row["tok_output"] or 0))
        return typed if typed > 0 else (row["tokens"] or 0)

    def _run_dict(self, r) -> dict:
        """Row → dict for a `run` row, with `tokens` overridden to the 3-type display amount (see
        _display_tokens) so every run-returning surface reconciles with the token dashboard default."""
        d = dict(r)
        d["tokens"] = self._display_tokens(r)
        return d

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            # SESSION — the durable, resumable container. Replaces `.sessions.json`. Keyed by
            # the SDK session id; `cwd` is retained because the SDK stores transcripts under an
            # encoded-cwd dir (resume needs it). `repo_id` is the logical key (resolved from cwd).
            c.execute(
                """CREATE TABLE IF NOT EXISTS session (
                       id TEXT PRIMARY KEY,
                       repo_id TEXT,
                       mode TEXT NOT NULL DEFAULT 'core',
                       surface TEXT NOT NULL DEFAULT 'web',
                       cwd TEXT NOT NULL,
                       thread_ts TEXT,
                       item_id TEXT,
                       resumable INTEGER NOT NULL DEFAULT 1,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # `item_id` is the durable, IMMUTABLE identity stamp (work-item-session-recognition-prd):
            # non-NULL ⇒ this is a WORK-ITEM session, permanently tied to one primary work-item;
            # NULL ⇒ a GENERAL (free-discussion) session. Set once by an actual work-item workflow
            # (never minted manually), it is the SINGLE source of truth the daemon reads to center
            # the agent + drive the write-sandbox / run-lock / telemetry. Additive ALTER below.
            self._ensure_columns(c, "session", {"item_id": "TEXT"})
            # `kind` is the session's AGENT IDENTITY (session-kinds-diagnose): one of
            # general | work_item | diagnosis (onboarding is a transient persona of general, not a
            # stamped kind). It selects which per-turn agent preamble the daemon assembles
            # (kernel_speech.py) and how the turn is centered/gated. `subject_run_id`
            # is the READ-ONLY pointer a subject-bearing kind carries — v1: a diagnosis session points
            # at the run it inspects (an Activity row). Both stamped write-once at the session's birth
            # (mirrors item_id). NULL kind ⇒ inferred from item_id (work_item) else general, so pre-
            # existing sessions keep working without a backfill. Additive ALTERs below.
            self._ensure_columns(c, "session", {"kind": "TEXT", "subject_run_id": "INTEGER"})
            # An owner-set TITLE override — NULL ⇒ the title is derived from the transcript (the SDK
            # `ai-title` bubble, else the first user line). When set, it wins in list/read so the
            # picker shows the owner's name. Additive ALTER below.
            self._ensure_columns(c, "session", {"title": "TEXT"})
            c.execute("CREATE INDEX IF NOT EXISTS idx_session_repo ON session(repo_id, mode)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_session_cwd ON session(cwd)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_session_item ON session(item_id)")
            # RUN — an execution: a turn within a session, OR a standalone workflow pass.
            # Written at START (status=running) so live + historical live in ONE table, ONE
            # query (decision 4). `session_id` is NULL for standalone workflow runs (distill),
            # so they never reach the resumable picker. The run-guard keys on (repo_id, mode,
            # feature). Disposable features purge their row on done; aborted rows are kept.
            c.execute(
                """CREATE TABLE IF NOT EXISTS run (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       repo_id TEXT NOT NULL,
                       mode TEXT NOT NULL DEFAULT 'dev',
                       feature TEXT NOT NULL DEFAULT 'chat',
                       session_id TEXT,
                       item_id TEXT,
                       status TEXT NOT NULL DEFAULT 'running',
                       model TEXT,
                       tokens INTEGER NOT NULL DEFAULT 0,
                       tok_input INTEGER NOT NULL DEFAULT 0,
                       tok_cache_creation INTEGER NOT NULL DEFAULT 0,
                       tok_cache_read INTEGER NOT NULL DEFAULT 0,
                       tok_output INTEGER NOT NULL DEFAULT 0,
                       raw_usage TEXT,
                       ctx_pct INTEGER,
                       phase TEXT,
                       started_at TEXT NOT NULL,
                       ended_at TEXT,
                       discarded_at TEXT
                   )"""
            )
            # Token accounting (token-usage-tracking-spec): the four Anthropic usage fields kept
            # SEPARATE (not fused into `tokens`) so both breakdowns — semantic (by feature) and
            # systematic (by token type) — reconcile against the SAME rows, and cache_read is NEVER
            # dropped. `raw_usage` preserves the complete SDK usage dict (forward-compat: a new usage
            # field is retained even before it earns a typed column). The legacy `tokens` scalar is
            # KEPT and still populated (= input+output+cache_creation) for back-compat with existing
            # telemetry/guard UIs, and it is what the LIVE in-flight counter bumps — so a run that
            # never returns a final usage carries it with four zero typed columns. The usage tables
            # simply skip those rows (measured only); the loop's budget breaker still counts them,
            # because a spend that happened is a spend. Additive ALTERs migrate an existing .system.db.
            # `discarded_at` — the re-run SOFT delete (owner, 2026-07-31). A re-run throws the
            # attempt away but never the rows: they are stamped, not deleted ([[never-delete-logs]]),
            # and the readers split. ITEM-SCOPED reads (this item's trace, its card totals, its loop
            # budget) filter to `discarded_at IS NULL` — the current attempt; AGGREGATE reads (repo
            # and system token totals, the Activity feed, run history) count everything, because the
            # discarded attempt really was paid for. So a repo total is legitimately larger than the
            # sum of its cards, and that is the honest number, not a bug.
            self._ensure_columns(c, "run", {
                "discarded_at": "TEXT",
                "tok_input": "INTEGER NOT NULL DEFAULT 0",
                "tok_cache_creation": "INTEGER NOT NULL DEFAULT 0",
                "tok_cache_read": "INTEGER NOT NULL DEFAULT 0",
                "tok_output": "INTEGER NOT NULL DEFAULT 0",
                "raw_usage": "TEXT",
                # The work-item phase this run happened IN (plan_design / build_eval / …), stamped at
                # run open so a work-item's tokens can be accumulated per-phase. NULL for chat/background
                # (non-item) runs and for item runs opened before this column existed.
                "phase": "TEXT",
                # The fate of this run's origin session (session-deletion-trace-model): NULL while the
                # session is live; 'deleted' (owner drop) or 'retired' (natural workflow end) once the
                # session is hard-deleted. The RUN itself is never deleted — this labels the trace whose
                # session is gone, so activity can show a "session deleted/retired" badge.
                "session_fate": "TEXT",
                # Per-run terminal OUTCOME (workspace-workflow D2, declared S1 / persisted S5): how a
                # BACKGROUND run's structured completion report ended — success | clean_noop | blocked |
                # approval_required | exhausted | stagnated. NULL for interactive turns (no report)
                # and pre-S5 rows. Feeds the status router + the attention engine (S7).
                "outcome": "TEXT",
            })
            c.execute("CREATE INDEX IF NOT EXISTS idx_run_guard ON run(repo_id, mode, feature, status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_run_item ON run(repo_id, item_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_run_session ON run(session_id)")
            # The per-item run-lock, enforced by the DB: at most one RUNNING row per (repo, item).
            # NULL item_ids (chat/distill/sweep runs) are distinct under SQLite's unique rules, so
            # only item-bound runs are constrained. Makes start_item_run provably atomic (a racing
            # second insert raises IntegrityError) rather than relying on a check-then-insert. (R5)
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_run_one_live"
                      " ON run(repo_id, item_id) WHERE status='running' AND item_id IS NOT NULL")
            # MODEL_OVERRIDE — per-repo runtime model preference. Replaces `.context_models.json`
            # ({context_id: model} → {repo_id: model}). A runtime preference, not static config,
            # so it's a DB row not a YAML edit.
            c.execute(
                """CREATE TABLE IF NOT EXISTS model_override (
                       repo_id TEXT PRIMARY KEY,
                       model TEXT,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # EFFORT_OVERRIDE — per-repo runtime reasoning-effort preference (low|medium|high),
            # mirroring model_override. A runtime preference, not static config.
            c.execute(
                """CREATE TABLE IF NOT EXISTS effort_override (
                       repo_id TEXT PRIMARY KEY,
                       effort TEXT,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # REPO_LEARNING — per-repo participation in automatic capture. The global master switch
            # (system_setting.learning_enabled) gates ALL learning; this row lets the owner opt a
            # single repo out even when the master is on. Absent = participate (default on).
            c.execute(
                """CREATE TABLE IF NOT EXISTS repo_learning (
                       repo_id TEXT PRIMARY KEY,
                       enabled INTEGER NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # REPO_AUTOPILOT — per-project autopilot tuning (workspace-workflow autopilot slice 3).
            # `concurrency` = max autopilot items in the build⟷vet loop at once for this repo
            # (the launch breaker; absent row = the default in get_autopilot_concurrency). Per-repo
            # by owner decision — the right width depends on the project, no global default.
            c.execute(
                """CREATE TABLE IF NOT EXISTS repo_autopilot (
                       repo_id TEXT PRIMARY KEY,
                       concurrency INTEGER NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # REPO_META — the owner's VISUAL tag for a repo: a display color + an optional icon
            # (emoji) shown in place of the color swatch. Purely presentational (the orbit/inspector
            # read it); absent = fall back to the hashed palette color + no icon.
            c.execute(
                """CREATE TABLE IF NOT EXISTS repo_meta (
                       repo_id TEXT PRIMARY KEY,
                       color TEXT,
                       icon TEXT,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # ARCHIVED_REPO — the tombstone a disconnected project leaves behind. Its runs are
            # preserved forever (never-delete-logs) but its registration is gone, so nothing could
            # name them: the activity log fell back to the raw id and its tokens dropped out of the
            # per-repo breakdown while still counting in the global total. This row keeps the label
            # (and when it left) so that spend stays attributable — aggregated under "Old projects".
            c.execute(
                """CREATE TABLE IF NOT EXISTS archived_repo (
                       repo_id TEXT PRIMARY KEY,
                       label TEXT NOT NULL,
                       cwd TEXT,
                       disconnected_at TEXT NOT NULL
                   )"""
            )
            # SYSTEM_SETTING — runtime overrides of the System singleton's static config
            # (config/system.yaml). A key/value row wins over the YAML default; absence = use
            # the YAML. Today's only key is `default_model` (the model floor below per-repo
            # overrides), set live from the System cockpit's Configure tab.
            c.execute(
                """CREATE TABLE IF NOT EXISTS system_setting (
                       key TEXT PRIMARY KEY,
                       value TEXT,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # RUN_ARTIFACT — the tools / sub-agents / skills a work-item's runs CALLED, one row
            # per invocation (the "call skill … / call subagent …" trail Claude Code prints).
            # Per-work-item telemetry, kept apart from the curated event LOG so it can't flood it.
            c.execute(
                """CREATE TABLE IF NOT EXISTS run_artifact (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       run_id INTEGER,
                       repo_id TEXT NOT NULL,
                       item_id TEXT NOT NULL,
                       seq INTEGER NOT NULL,
                       kind TEXT NOT NULL,
                       name TEXT NOT NULL,
                       description TEXT,
                       tool_id TEXT,
                       created_at TEXT NOT NULL,
                       discarded_at TEXT
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_artifact_item ON run_artifact(repo_id, item_id)")
            # RUN_EVENT — the per-RUN observability trail (any run, incl. session-less background):
            # the prompt that triggered it, the assistant's reply text, and every tool/skill/agent
            # call, in `seq` order. Keyed by `run_id` (item_id optional) so each Activity row — one
            # run — has its own thread, distinct from the whole-session transcript. Kept as durable
            # telemetry like the run row (the throwaway SDK transcript is separate).
            c.execute(
                """CREATE TABLE IF NOT EXISTS run_event (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       run_id INTEGER,
                       repo_id TEXT NOT NULL,
                       item_id TEXT,
                       seq INTEGER NOT NULL,
                       kind TEXT NOT NULL,
                       name TEXT NOT NULL,
                       description TEXT,
                       tool_id TEXT,
                       created_at TEXT NOT NULL
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_event_run ON run_event(run_id)")
            # Stamped alongside `run.discarded_at` by a re-run — the per-item trace pane reads this
            # table directly (it is a strict superset of run_artifact), so filtering only `run`
            # would leave the discarded attempt's tool calls on screen.
            self._ensure_columns(c, "run_event", {"discarded_at": "TEXT"})
            # tool_id (the SDK tool_use id) pairs a `result` row back to its CALL row — concurrent
            # tools return out of call order, so position/name can't pair them; the id can. Additive.
            # parent_tool_id (the SDK `parent_tool_use_id`) is the tool_use id of the sub-agent SPAWN
            # a row happened inside — NULL for the parent's own calls. A fan-out interleaves its
            # children into one stream, so this is what lets a reader nest "which agent ran this"
            # instead of reading three parallel readers as one confused agent. Additive.
            self._ensure_columns(c, "run_artifact", {"tool_id": "TEXT", "parent_tool_id": "TEXT"})
            self._ensure_columns(c, "run_event", {"tool_id": "TEXT", "parent_tool_id": "TEXT"})
            # SWEEP_WATERMARK — the capture sweep's per-session swept position (WI-8). `position`
            # is the count of chat messages already swept for a session; every sweep advances it
            # to the transcript head, so a message is NEVER swept twice (content-level idempotency).
            # Deterministic, server-truth — the watermark, not the LLM, decides what's already seen.
            c.execute(
                """CREATE TABLE IF NOT EXISTS sweep_watermark (
                       session_id TEXT PRIMARY KEY,
                       position INTEGER NOT NULL DEFAULT 0,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # RUN_INPUT — the prompt inspector's "A" capture: the ACTUAL full input a run sent, the
            # assembled system prompt (layer-2 append) + the prompt body, one row per run. Uncapped
            # (unlike run_event's short trail rows) so the exact bytes survive. Written once at send
            # time; durable telemetry like the run row (never deleted — never-delete-logs).
            c.execute(
                """CREATE TABLE IF NOT EXISTS run_input (
                       run_id INTEGER PRIMARY KEY,
                       repo_id TEXT NOT NULL,
                       item_id TEXT,
                       phase TEXT,
                       feature TEXT,
                       background INTEGER NOT NULL DEFAULT 0,
                       system_prompt TEXT NOT NULL,
                       prompt_body TEXT NOT NULL,
                       system_fragments TEXT,
                       created_at TEXT NOT NULL
                   )"""
            )
            # `system_fragments` (JSON: ordered [{name,location,text}] the system prompt is assembled
            # from) added after the table shipped — the prompt inspector renders per-fragment sub-cards
            # with source provenance. Older rows have NULL → the inspector falls back to one whole card.
            # `turn_surface` (JSON) added 2026-08-06: the prompt is only half of what shapes a turn.
            # The other half is what the turn is ALLOWED to do — which MCP servers it carries, where
            # it may write, which tools are dead, whether the shell is sandboxed, and on which model
            # at which effort. A capture showing prose alone can't explain why two runs on the same
            # words behaved differently. Older rows are NULL → the inspector omits the block.
            # `authored_extras` (JSON) added 2026-08-10: the system append is not the whole of what
            # SuperMe authors. Two more channels ride in every request and were invisible here —
            # the phase SKILL.md the trigger tells the run to invoke (the single largest authored
            # artifact), and the description + parameter docs of the MCP tools the turn mounts.
            # A page showing only the append understates our own share of the prefix. Older rows
            # are NULL → the inspector omits both sections.
            self._ensure_columns(c, "run_input",
                                 {"system_fragments": "TEXT", "turn_surface": "TEXT",
                                  "authored_extras": "TEXT"})

    # --- static config (loaded fresh; cheap + always current) -------------------
    def system_config(self) -> SystemConfig:
        return load_system_config(self._system_config_path)

    def repos(self) -> dict[str, RepoConfig]:
        return load_repos(self._repos_config_path)

    def repo(self, repo_id: str) -> RepoConfig | None:
        return self.repos().get(repo_id)

    def add_repo(self, rc: RepoConfig) -> RepoConfig:
        """Register a new repo: APPEND its entry to config/repos.yaml (text-append, not a whole-file
        re-dump — so the header comments + existing formatting survive). Since `repos()` re-reads the
        file every call, the repo goes live at once — no restart. The knowledge home is NOT seeded:
        it's created lazily on first write, and onboarding authors general/ from scratch. Raises
        ValueError on a duplicate id or cwd."""
        import textwrap
        current = self.repos()
        if rc.id in current:
            raise ValueError(f"repo id '{rc.id}' already exists")
        if any(_norm(x.cwd) == _norm(rc.cwd) for x in current.values()):
            raise ValueError(f"a repo is already connected at {rc.cwd}")
        # The new entry keyed by id (the id key is redundant under the block, so drop it).
        spec = {k: v for k, v in rc.to_dict().items() if k != "id"}
        block = textwrap.indent(yaml.safe_dump({rc.id: spec}, sort_keys=False), "  ")
        path = Path(self._repos_config_path)
        text = path.read_text() if path.exists() else ""
        if "repos:" not in text:                       # fresh/empty file → start the mapping
            text = (text.rstrip() + "\n\nrepos:\n") if text.strip() else "repos:\n"
        elif not text.endswith("\n"):
            text += "\n"
        path.write_text(text + block)
        return rc

    def update_repo(self, repo_id: str, **fields) -> RepoConfig:
        """Edit scalar fields on an EXISTING repos.yaml entry, in place and line-by-line — so the
        header comments, the other entries, and this entry's own untouched lines keep their exact
        bytes (a whole-file yaml.safe_dump would flatten all three). A field set to None deletes its
        line; a field absent from `fields` is left alone. Validates by re-loading before returning,
        so a malformed write can never go live silently. Raises ValueError on an unknown repo or an
        unknown/invalid field."""
        rc = self.repos().get(repo_id)
        if rc is None:
            raise ValueError(f"unknown repo id '{repo_id}'")
        allowed = {"review_mode", "anchor_branch", "label", "persona_append", "onboarding"}
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update {sorted(unknown)} — editable fields: {sorted(allowed)}")
        if "review_mode" in fields and fields["review_mode"] not in REVIEW_MODES:
            raise ValueError(f"review_mode must be one of {list(REVIEW_MODES)}")
        # Normalize: "" clears a field (the UI's empty input), and clearing means "drop the line".
        patch = {k: (v.strip() if isinstance(v, str) else v) or None for k, v in fields.items()}
        # A field set back to its DEFAULT is stored as absence, matching `to_dict` — so "unset" and
        # "explicitly the default" have one representation, and setting a knob and setting it back
        # leaves the file byte-identical.
        if patch.get("review_mode") == REVIEW_MODE_DEFAULT:
            patch["review_mode"] = None
        def _line(key: str, val) -> str:
            # Dump as a one-key mapping, not a bare scalar: safe_dump of a scalar emits a document
            # (`develop\n...\n`), while the mapping form yields exactly `key: value`, quoted as YAML
            # requires. Indented to 4 to match every other field in the entry.
            return "    " + yaml.safe_dump({key: val}, sort_keys=False).strip() + "\n"

        path = Path(self._repos_config_path)
        text = path.read_text()
        if text and not text.endswith("\n"):   # else an appended key would join the last line
            text += "\n"
        lines = text.splitlines(keepends=True)
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            ln = lines[i]
            out.append(ln)
            i += 1
            if not (ln.rstrip().startswith(f"  {repo_id}:") and not ln.startswith("   ")):
                continue
            # Inside the entry: rewrite matching keys, keep everything else verbatim.
            seen: set[str] = set()
            while i < n and lines[i].startswith("    "):
                key = lines[i].split(":", 1)[0].strip()
                if key in patch:
                    seen.add(key)
                    if patch[key] is not None:
                        out.append(_line(key, patch[key]))
                    # else: drop the line — the field is cleared
                else:
                    out.append(lines[i])
                i += 1
            for key, val in patch.items():          # keys the entry didn't carry yet
                if key not in seen and val is not None:
                    out.append(_line(key, val))
        path.write_text("".join(out))
        updated = self.repos().get(repo_id)
        if updated is None:
            raise ValueError(f"repos.yaml update dropped '{repo_id}' — file left as written; "
                             "inspect config/repos.yaml")
        return updated

    def remove_repo(self, repo_id: str) -> RepoConfig:
        """Deregister a repo (disconnect): surgically drop its entry block from config/repos.yaml —
        a line-level edit, so header comments + every other entry keep their exact formatting — and
        delete its runtime kv rows (model/effort/learning/meta overrides). This only forgets the
        REGISTRATION; the knowledge home, harness cell, sessions and worktrees are the disconnect
        route's concern. Refuses 'global' (the hub is not disconnectable) and unknown ids.
        Like add_repo, goes live at once — repos() re-reads the file every call."""
        if repo_id == "global":
            raise ValueError("the hub repo cannot be disconnected")
        rc = self.repos().get(repo_id)
        if rc is None:
            raise ValueError(f"unknown repo id '{repo_id}'")
        path = Path(self._repos_config_path)
        out, dropping = [], False
        for ln in path.read_text().splitlines(keepends=True):
            if not dropping and ln.rstrip().startswith(f"  {repo_id}:") and not ln.startswith("   "):
                dropping = True
                continue
            if dropping:
                if ln.startswith("    "):  # the entry's nested fields (safe_dump indents them to 4)
                    continue
                dropping = False  # anything shallower ends the block — keep it
            out.append(ln)
        path.write_text("".join(out))
        with self._conn() as c:
            for table in ("model_override", "effort_override", "repo_learning", "repo_meta"):
                c.execute(f"DELETE FROM {table} WHERE repo_id=?", (repo_id,))
            # Leave the tombstone so this repo's preserved runs stay nameable + attributable.
            c.execute(
                "INSERT INTO archived_repo (repo_id,label,cwd,disconnected_at) VALUES (?,?,?,?)"
                " ON CONFLICT(repo_id) DO UPDATE SET label=excluded.label, cwd=excluded.cwd,"
                " disconnected_at=excluded.disconnected_at",
                (repo_id, rc.label, str(rc.cwd), _now()),
            )
        return rc

    def archived_repos(self) -> dict[str, dict]:
        """{repo_id: {label, cwd, disconnected_at}} for every disconnected project. Reconnecting
        the same id doesn't clear the tombstone — it's harmless, since every attribution path
        checks the LIVE roster first and only falls through to here."""
        with self._conn() as c:
            return {r["repo_id"]: dict(r)
                    for r in c.execute("SELECT * FROM archived_repo").fetchall()}

    def repo_for_cwd(self, cwd) -> str | None:
        """Reverse-resolve a cwd to a repo id (the logical key for a session)."""
        target = _norm(cwd)
        for rid, rc in self.repos().items():
            if _norm(rc.cwd) == target:
                return rid
        return None

    # --- startup reconcile ------------------------------------------------------
    def reconcile(self) -> list[dict]:
        """On daemon startup, flip orphaned `running` runs → `aborted` (the daemon that owned
        them is gone). Leaves an auditable trace instead of a silent vanish.

        Returns one row per orphan — `{run_id, repo_id, item_id, phase, feature}` — so the caller
        can heal the OTHER half. Healing the run row alone left the work-item at `active` with no
        run and nothing that would ever start one: permanently wedged, and silent until the
        attention engine learned to page for it (dogfood D2/D3, 2026-07-29). The rows are read
        BEFORE the update, because the flip is what destroys the evidence of which ones they were.
        `len()` is the old count, so a caller that only wanted the number still works."""
        with self._conn() as c:
            orphans = [dict(r) for r in c.execute(
                "SELECT id AS run_id, repo_id, item_id, phase, feature FROM run"
                " WHERE status='running' AND discarded_at IS NULL"
            ).fetchall()]
            c.execute(
                "UPDATE run SET status='aborted', ended_at=? WHERE status='running'",
                (_now(),),
            )
        if orphans:
            log.info("spine reconcile: %d orphaned run(s) → aborted", len(orphans))
        return orphans

    # --- sessions ---------------------------------------------------------------
    def record_session(self, session_id: str, cwd, *, surface: str = "web",
                       mode: str = "core", thread_ts: str | None = None,
                       repo_id: str | None = None, resumable: bool = True) -> dict | None:
        """Upsert a session. Sets repo_id/mode/surface/created_at once; refreshes updated_at
        every turn (the former `.sessions.json` upsert semantics). repo_id auto-resolves from cwd."""
        if not session_id:
            return None
        cwd = _norm(cwd)
        rid = repo_id or self.repo_for_cwd(cwd)
        now = _now()
        with self._conn() as c:
            row = c.execute("SELECT * FROM session WHERE id=?", (session_id,)).fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO session (id,repo_id,mode,surface,cwd,thread_ts,resumable,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (session_id, rid, mode, surface, cwd, thread_ts, int(resumable), now, now),
                )
            else:
                # Refresh cwd + updated_at; backfill repo_id/thread_ts once (never clobber).
                c.execute(
                    "UPDATE session SET cwd=?, updated_at=?,"
                    " repo_id=COALESCE(repo_id,?), thread_ts=COALESCE(thread_ts,?) WHERE id=?",
                    (cwd, now, rid, thread_ts, session_id),
                )
            return self._get_session(c, session_id)

    def get_session(self, session_id: str) -> dict | None:
        with self._conn() as c:
            return self._get_session(c, session_id)

    def session_item(self, session_id: str | None) -> str | None:
        """The work-item this session is stamped to, or None for a general session (or unknown id).
        This stamp is the SINGLE source of truth for whether a session is a work-item session —
        the daemon reads it to center the agent + drive the sandbox / run-lock / telemetry."""
        if not session_id:
            return None
        with self._conn() as c:
            r = c.execute("SELECT item_id FROM session WHERE id=?", (session_id,)).fetchone()
            return (r["item_id"] if r else None) or None

    def session_is_onboarding(self, session_id: str | None) -> bool:
        """True if this session ever ran an onboarding turn (any `feature='onboarding'` run on it).
        Onboarding sessions are SuperMe walking the owner through project-init/retrofit — the turns
        are dense with SuperMe reciting its own skills/guides, so the capture sweep skips them
        wholesale (the owner-anchored capture filter is the semantic backstop; this is the coarse
        structural one). A one-time, any-project process → nothing durable to mine from it."""
        if not session_id:
            return False
        with self._conn() as c:
            r = c.execute(
                "SELECT 1 FROM run WHERE session_id=? AND feature='onboarding' LIMIT 1",
                (session_id,)).fetchone()
            return r is not None

    def session_is_diagnosis(self, session_id: str | None) -> bool:
        """True if this session is a DIAGNOSIS session (kind='diagnosis'). Diagnosis is read-only
        meta-observation ABOUT the work, not the work — so capture never sweeps it (mirrors the
        onboarding skip): mining it would feed 'diagnosis of a diagnosis' recursion and log noise.
        Diagnosis runs still land in Activity + token accounting; only the learning sweep skips them."""
        if not session_id:
            return False
        with self._conn() as c:
            r = c.execute(
                "SELECT 1 FROM session WHERE id=? AND kind='diagnosis' LIMIT 1", (session_id,),
            ).fetchone()
            return r is not None

    def stamp_session_item(self, session_id: str, item_id: str) -> bool:
        """Stamp a session's durable work-item identity — write-once (IMMUTABLE): only sets item_id
        when it is currently NULL, so a work-item session can never be re-pointed to a different item
        (work-item-session-recognition-prd). Idempotent if already stamped to the same item. Returns
        True if this call set it. Called at the two existing session-persistence points (bound-turn
        finish, background /plan) — never from a user-facing 'create session' path, which is what keeps
        work-item sessions from being minted manually."""
        if not session_id or not item_id:
            return False
        with self._conn() as c:
            cur = c.execute(
                "UPDATE session SET item_id=?, updated_at=? WHERE id=? AND item_id IS NULL",
                (item_id, _now(), session_id),
            )
            return cur.rowcount > 0

    def stamp_session_kind(self, session_id: str, kind: str,
                           subject_run_id: int | None = None) -> bool:
        """Stamp a session's durable KIND + optional subject pointer — write-once (only sets `kind`
        while it is NULL), mirroring stamp_session_item (session-kinds-diagnose). Called at a
        subject-bearing session's birth (a diagnosis session's first-turn finish) so the daemon reads
        the stored kind on resume rather than trusting the client. work_item is stamped implicitly by
        stamp_session_item (item_id ⇒ work_item), so this is for diagnosis/onboarding/general births
        that carry no item. Returns True if this call set it."""
        if not session_id or not kind:
            return False
        with self._conn() as c:
            cur = c.execute(
                "UPDATE session SET kind=?, subject_run_id=?, updated_at=?"
                " WHERE id=? AND kind IS NULL",
                (kind, subject_run_id, _now(), session_id),
            )
            return cur.rowcount > 0

    def set_session_title(self, session_id: str, title: str | None) -> bool:
        """Set (or clear) a session's owner TITLE override. A blank title clears it back to NULL so the
        title reverts to the transcript-derived one. Returns True if a row was updated."""
        if not session_id:
            return False
        clean = (title or "").strip() or None
        with self._conn() as c:
            cur = c.execute(
                "UPDATE session SET title=?, updated_at=? WHERE id=?",
                (clean, _now(), session_id),
            )
            return cur.rowcount > 0

    def session_kind(self, session_id: str | None) -> dict | None:
        """The stored (kind, subject_run_id) for a session, or None if unknown/unstamped. `kind` is
        NULL for pre-existing sessions — the caller derives it (item_id ⇒ work_item else general)."""
        if not session_id:
            return None
        with self._conn() as c:
            r = c.execute(
                "SELECT kind, subject_run_id, item_id FROM session WHERE id=?", (session_id,),
            ).fetchone()
            return dict(r) if r else None

    def backfill_session_items(self, pairs: list[tuple[str, str]]) -> int:
        """One-time migration: stamp each (session_id, item_id) whose session row is not yet stamped,
        so work-items already carrying a session_id aren't stranded as 'general'. Write-once, so it
        never clobbers an existing stamp. Returns the number of rows newly stamped."""
        n = 0
        with self._conn() as c:
            for session_id, item_id in pairs:
                if not session_id or not item_id:
                    continue
                cur = c.execute(
                    "UPDATE session SET item_id=? WHERE id=? AND item_id IS NULL",
                    (item_id, session_id),
                )
                n += cur.rowcount
        return n

    def sessions_for_cwd(self, cwd, *, resumable_only: bool = True) -> list[dict]:
        """Sessions that ran in a workspace (the web picker). Resumable-only by design — a
        standalone workflow never creates a Session, so this can't surface one."""
        where = ["cwd=?"]
        args: list = [_norm(cwd)]
        if resumable_only:
            where.append("resumable=1")
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM session WHERE {' AND '.join(where)}"
                " ORDER BY datetime(updated_at) DESC", args,
            ).fetchall()
            return [dict(r) for r in rows]

    def sessions_for_repo(self, repo_id: str, *, resumable_only: bool = False) -> list[dict]:
        """Every session row bound to a repo — the WORKSPACE's threads, wherever they ran.

        `repo_id` is the identity that survives a worktree: a phase run executes with its cwd
        swapped to `~/.superme/worktrees/<repo>/<item>`, but it is still this workspace's thread, so
        anything asking "what has been said in this workspace" must ask by repo and not by cwd.

        Default is EVERY row, resumable or not — the disconnect cascade walks these to hard-delete
        each through the session-deletion-trace-model. The picker passes `resumable_only`."""
        where = ["repo_id=?"]
        if resumable_only:
            where.append("resumable=1")
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM session WHERE {' AND '.join(where)}"
                " ORDER BY datetime(updated_at) DESC", (repo_id,)).fetchall()
            return [dict(r) for r in rows]

    def running_run_count(self, repo_id: str) -> int:
        """Runs executing right now for a repo (any mode/feature) — the disconnect pre-flight
        guard: never rip a repo out from under a live agent."""
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM run WHERE repo_id=? AND status='running'",
                             (repo_id,)).fetchone()[0]

    def session_for_thread(self, thread_ts: str) -> str | None:
        """The session id for a Slack thread (thread_ts is globally unique)."""
        with self._conn() as c:
            r = c.execute("SELECT id FROM session WHERE thread_ts=?", (thread_ts,)).fetchone()
            return r["id"] if r else None

    def delete_session_record(self, session_id: str, *, cause: str = "deleted") -> bool:
        """The one db-layer session removal (session-deletion-trace-model). Hard-deletes the
        session's resume STATE — the `session` row + its transient `sweep_watermark` — and LABELS the
        trace it leaves behind. The session's runs and their run_events + run_artifacts are the
        permanent activity + token record and are **preserved** (monitoring logs are never deleted):
        each run is stamped `session_fate=cause` so the activity log can show its origin session is
        gone and why. Any still-live run is closed out (`running→aborted`, the boot reconciler's
        terminal status) so nothing lingers as a ghost. KNOWLEDGE is never touched (general docs /
        work-items / memory are repo/item-keyed — operational ⟂ knowledge). Orphaned `session_id` on
        a preserved run is harmless: no read path joins `session`, so orphaned runs still surface in
        the activity log + token stats. `cause`: 'deleted' (owner drop) | 'retired' (natural workflow
        end). Idempotent; returns True if a session row existed. (Transcript-FILE deletion lives in
        SessionStore — the spine owns only the db.)"""
        if not session_id:
            return False
        now = _now()
        with self._conn() as c:
            c.execute("UPDATE run SET status='aborted', ended_at=? WHERE session_id=? AND status='running'",
                      (now, session_id))
            c.execute("UPDATE run SET session_fate=? WHERE session_id=?", (cause, session_id))
            c.execute("DELETE FROM sweep_watermark WHERE session_id=?", (session_id,))
            cur = c.execute("DELETE FROM session WHERE id=?", (session_id,))
            return cur.rowcount > 0

    # --- capture-sweep watermark (WI-8) -----------------------------------------
    def get_sweep_watermark(self, session_id: str) -> int:
        """The count of chat messages already swept for this session (0 if never swept)."""
        with self._conn() as c:
            r = c.execute("SELECT position FROM sweep_watermark WHERE session_id=?",
                          (session_id,)).fetchone()
            return int(r["position"]) if r else 0

    def set_sweep_watermark(self, session_id: str, position: int) -> None:
        """Advance (upsert) the swept position to the transcript head after a clean sweep."""
        with self._conn() as c:
            c.execute(
                "INSERT INTO sweep_watermark (session_id,position,updated_at) VALUES (?,?,?)"
                " ON CONFLICT(session_id) DO UPDATE SET position=excluded.position,"
                " updated_at=excluded.updated_at",
                (session_id, int(position), _now()),
            )

    def forget_thread(self, thread_ts: str) -> bool:
        """Drop every session bound to a Slack thread (parity with the former forget_thread)."""
        with self._conn() as c:
            cur = c.execute("DELETE FROM session WHERE thread_ts=?", (thread_ts,))
            return cur.rowcount > 0

    def _get_session(self, c: sqlite3.Connection, session_id: str) -> dict | None:
        r = c.execute("SELECT * FROM session WHERE id=?", (session_id,)).fetchone()
        return dict(r) if r else None

    # --- runs -------------------------------------------------------------------
    def start_run(self, repo_id: str, *, mode: str = "dev", feature: str = "chat",
                  session_id: str | None = None, item_id: str | None = None,
                  model: str | None = None) -> int:
        """Open a run row (status=running) and return its id. Live + historical share this
        table, so the dashboard reads one source. session_id stays NULL for standalone
        workflow runs (distill) — that's what keeps them out of the picker."""
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO run (repo_id,mode,feature,session_id,item_id,status,model,started_at)"
                " VALUES (?,?,?,?,?,'running',?,?)",
                (repo_id, mode, feature, session_id, item_id, model, _now()),
            )
            return cur.lastrowid

    def start_item_run(self, repo_id: str, *, mode: str = "dev", feature: str = "plan",
                       item_id: str, model: str | None = None, phase: str | None = None) -> int | None:
        """Atomically open a run for a work-item IFF none is already in flight for it — the per-item
        run-lock enforced at the data layer (R5). The cheap SELECT short-circuits the common case, but
        the guarantee is the `idx_run_one_live` UNIQUE index: a racing second insert raises
        IntegrityError and we return None, so two running rows for one item are impossible regardless
        of interleaving. Returns the new run id, or None if the item was already running."""
        import sqlite3
        with self._conn() as c:
            busy = c.execute(
                "SELECT 1 FROM run WHERE repo_id=? AND item_id=? AND status='running' LIMIT 1",
                (repo_id, item_id),
            ).fetchone()
            if busy:
                return None
            try:
                cur = c.execute(
                    "INSERT INTO run (repo_id,mode,feature,session_id,item_id,status,model,phase,started_at)"
                    " VALUES (?,?,?,?,?,'running',?,?,?)",
                    (repo_id, mode, feature, None, item_id, model, phase, _now()),
                )
            except sqlite3.IntegrityError:
                return None  # lost the race — another begin opened the run first
            return cur.lastrowid

    def bump_run(self, run_id: int, *, add_tokens: int = 0,
                 ctx_pct: int | None = None) -> None:
        """Live-update a running row from one Usage step (a LIVE in-flight estimate only): accumulate
        the legacy `tokens` counter + latest-wins context fill. The AUTHORITATIVE per-type accounting
        is written once at finish from the whole-turn Result usage (see finish_run) — because per-step
        SDK Usage events are cumulative-for-the-turn snapshots, summing them would over-count."""
        sets = ["tokens = tokens + ?"]
        args: list = [int(add_tokens or 0)]
        if ctx_pct is not None:
            sets.append("ctx_pct=?")
            args.append(int(ctx_pct))
        args.append(run_id)
        with self._conn() as c:
            c.execute(f"UPDATE run SET {','.join(sets)} WHERE id=?", args)

    def finish_run(self, run_id: int, *, status: str = "done", tokens: int | None = None,
                   usage: dict | None = None, ctx_pct: int | None = None,
                   model: str | None = None, session_id: str | None = None) -> None:
        """Close a run row (status done|aborted) — ALWAYS kept, as the durable run log/telemetry.
        For session-disposable features the throwaway *transcript* is deleted by the caller (the
        run record itself stays). status falls back to 'done' if unknown. `model` records the model
        the SDK actually resolved for the run (background features don't request one up front).

        `usage` is the whole-turn final SDK usage dict — the AUTHORITATIVE per-type accounting for
        the run (one run == one turn). finish sets the four typed columns absolutely from it and
        reconciles the legacy `tokens` scalar to match. Per-step bumps are a live estimate only."""
        status = status if status in _RUN_STATUSES else "done"
        sets = ["status=?", "ended_at=?"]
        args: list = [status, _now()]
        if tokens is not None:
            sets.append("tokens=?")
            args.append(int(tokens))
        if ctx_pct is not None:
            sets.append("ctx_pct=?")
            args.append(int(ctx_pct))
        if model:
            sets.append("model=?")
            args.append(model)
        if session_id:
            sets.append("session_id=?")
            args.append(session_id)
        args.append(run_id)
        with self._conn() as c:
            c.execute(f"UPDATE run SET {','.join(sets)} WHERE id=?", args)
            self._finish_usage_apply(c, run_id, usage)

    def _finish_usage_apply(self, c: sqlite3.Connection, run_id: int, usage: dict | None) -> None:
        """Write the per-type accounting at finish. Set the four typed columns absolutely from
        `usage`, store the raw blob, and reconcile the legacy `tokens` scalar to match. No-op when
        no usage arrived (e.g. an errored turn) — whatever the row accumulated is kept.

        **`usage` MUST be the whole turn's — parent AND subagents.** Callers pass the accumulated
        per-message total (`services/runs._LiveTokens.usage`), NOT `Result.usage`. That was the bug
        here until 2026-08-14: `Result.usage` covers the parent conversation's calls only, so
        overwriting with it silently REPLACED a full count with a partial one — measured 8.3x
        smaller on a fully-delegated turn and ~3x on two real research sweeps. The word
        "authoritative" was doing the damage: it made the smaller number look like the truth."""
        if not usage:
            return
        i, cc, cr, o = self._usage_parts(usage)
        c.execute(
            "UPDATE run SET tok_input=?, tok_cache_creation=?, tok_cache_read=?, tok_output=?,"
            " raw_usage=?, tokens=? WHERE id=?",
            (i, cc, cr, o, json.dumps(usage), self._legacy_tokens(i, cc, cr, o), run_id))

    def is_running(self, repo_id: str, mode: str, feature: str | None = None) -> bool:
        """The server-truth run-guard (decision 4). Correctly keyed by (repo × mode [× feature]) —
        this is what fixes the distill repo-only-guard defect, read from the DB not a shadow set."""
        where = ["repo_id=?", "mode=?", "status='running'"]
        args: list = [repo_id, mode]
        if feature is not None:
            where.append("feature=?")
            args.append(feature)
        with self._conn() as c:
            r = c.execute(f"SELECT 1 FROM run WHERE {' AND '.join(where)} LIMIT 1", args).fetchone()
            return r is not None

    def live_runs(self, repo_id: str | None = None) -> list[dict]:
        """All in-flight runs (status=running), optionally for one repo — the System entity's
        live half + the dashboard's 'what's happening now'."""
        where = ["status='running'"]
        args: list = []
        if repo_id is not None:
            where.append("repo_id=?")
            args.append(repo_id)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM run WHERE {' AND '.join(where)} ORDER BY datetime(started_at) DESC",
                args,
            ).fetchall()
            return [self._run_dict(r) for r in rows]

    def live_item_runs_quiet_since(self) -> list[dict]:
        """Every in-flight WORK-ITEM run with the timestamp of its most recent sign of life —
        `{run, quiet_since}`, where `quiet_since` is the last `run_event` for that run, or the run's
        own `started_at` when it has emitted nothing at all.

        The substrate of the stall watchdog (daemon/services/watchdog.py). ONE query rather than an
        events-per-run walk, because this is polled: `events_for_run` returns a run's whole trail,
        and a 300-event investigation would be re-read every tick to learn one timestamp.

        Item runs only. A chat turn is watched by the person who started it; an item run is the
        unattended kind, and the one that went silent for 24 minutes on 2026-08-14 with nobody the
        wiser. Timestamps are the ISO strings both tables already store — comparable as text
        because `_now()` writes one format, which is also why this returns them raw and leaves the
        threshold to the caller (the RULE is the watchdog's; the READ is the spine's)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT r.*, COALESCE(MAX(e.created_at), r.started_at) AS quiet_since"
                "  FROM run r LEFT JOIN run_event e ON e.run_id = r.id"
                " WHERE r.status='running' AND r.item_id IS NOT NULL"
                " GROUP BY r.id ORDER BY datetime(r.started_at) ASC"
            ).fetchall()
            return [{**self._run_dict(r), "quiet_since": r["quiet_since"]} for r in rows]

    def runs_for_item(self, repo_id: str, item_id: str) -> list[dict]:
        """Every run this work-item has had, OLDEST-first — the spine of the F2 unified timeline
        (each run is one phase agent's turn(s); ordering them chronologically reads triage→plan→
        build→vet→review as one conversation). Unbounded by design: an item's whole history, never
        truncated by a repo-wide limit."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM run WHERE repo_id=? AND item_id=? AND discarded_at IS NULL"
                " ORDER BY datetime(started_at) ASC, id ASC", (repo_id, item_id),
            ).fetchall()
            return [self._run_dict(r) for r in rows]

    def subagent_count(self, repo_id: str, item_id: str, *, phase: str) -> int:
        """How many SUBAGENTS this item's runs at `phase` actually spawned.

        The one unfakeable answer to "did this phase fan out". It counts `run_event` rows the kernel
        writes when the SDK reports an Agent call — the agent cannot write, omit or embellish them,
        which is the whole point: an artifact section saying "I split by module" is a claim, and this
        is the receipt. Discarded runs are excluded so a re-run's history doesn't inflate the count.

        Reads across EVERY run at that phase, not just the newest: a send-back means investigate ran
        twice, and fanning out on either pass is fanning out."""
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM run_event e JOIN run r ON r.id = e.run_id"
                " WHERE r.repo_id=? AND r.item_id=? AND r.phase=?"
                "   AND e.kind='subagent' AND e.discarded_at IS NULL AND r.discarded_at IS NULL",
                (repo_id, item_id, phase),
            ).fetchone()
            return int(row["n"] if row else 0)

    def brief_sizes(self, repo_id: str, item_id: str, *, phase: str) -> list[int]:
        """How big each subagent BRIEF was, for every spawn this item's runs made at `phase`.

        The companion to `subagent_count`, which answers "did it fan out" and cannot answer "did the
        workers get anything to work with". A subagent inherits nothing — not this skill, not the
        family guide, not the plan — so a two-line brief sends a reader out with no bar, and its
        findings come back looking exactly like findings written to one.

        Size is a PROXY and is read as one: it can prove a brief too short to have carried a bar,
        never that a long one carried the right bar. The trace row records `brief <n>` (written by
        `runs._artifact_desc`); spawns from before that landed carry no size and are skipped, so an
        older item reports fewer sizes than spawns rather than a pile of fake zeros.

        Returns one size per spawn that recorded one, newest run last. Empty list = no spawn ever
        recorded a size, which is not the same as a thin brief and must not be scored as one."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT e.description AS d FROM run_event e JOIN run r ON r.id = e.run_id"
                " WHERE r.repo_id=? AND r.item_id=? AND r.phase=?"
                "   AND e.kind='subagent' AND e.description LIKE '%brief %'"
                "   AND e.discarded_at IS NULL AND r.discarded_at IS NULL"
                " ORDER BY e.id ASC",
                (repo_id, item_id, phase),
            ).fetchall()
        sizes: list[int] = []
        for row in rows:
            tail = str(row["d"] or "").rsplit("brief ", 1)[-1]
            digits = "".join(ch for ch in tail if ch.isdigit())
            if digits:
                sizes.append(int(digits))
        return sizes

    def read_hits(self, repo_id: str, item_id: str, *, phase: str, needle: str) -> int:
        """How many times this item's runs at `phase` READ a path containing `needle`.

        The receipt for a directed read, and the sibling of `subagent_count`: the kernel logs a
        `run_event` per tool call, so "did it open the file the skill told it to open" is a count,
        not a claim the record makes about itself. Measured 2026-08-13: five of nine investigate
        runs never opened their family guide, and nothing anywhere said so — the record still came
        out in the right SHAPE, because the scaffolder stamps that from the template whether or not
        the method was ever read.

        Matched against the trace's own short-path description (last four segments), so the needle
        should be a tail like `investigate/references/audit.md`. Across every run at the phase: a
        send-back means investigate ran twice, and reading the guide on either pass counts."""
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM run_event e JOIN run r ON r.id = e.run_id"
                " WHERE r.repo_id=? AND r.item_id=? AND r.phase=?"
                "   AND e.kind='tool' AND e.name='Read' AND e.description LIKE ?"
                "   AND e.discarded_at IS NULL AND r.discarded_at IS NULL",
                (repo_id, item_id, phase, f"%{needle}%"),
            ).fetchone()
            return int(row["n"] if row else 0)

    def last_phase_run_end(self, repo_id: str, item_id: str, *, phase: str) -> str | None:
        """When this item's most recent FINISHED run at `phase` ended (ISO-8601 UTC), or None if
        the phase has never completed a run.

        The cutoff for "what changed since this thread last ran". Per-phase sessions mean a phase
        re-entered after a revise round resumes an agent that already formed a judgment — and an
        agent with a memory of the item will trust it over the disk unless told otherwise. This is
        the only honest clock for that: the phase's own last run, not the item's.

        `ended_at IS NOT NULL` excludes the run that is asking (the runner's row is already open by
        the time it computes its trigger) and any run that died mid-flight — a run that never
        finished never formed the judgment we are correcting for."""
        with self._conn() as c:
            row = c.execute(
                "SELECT MAX(ended_at) AS t FROM run"
                " WHERE repo_id=? AND item_id=? AND phase=?"
                "   AND ended_at IS NOT NULL AND discarded_at IS NULL",
                (repo_id, item_id, phase),
            ).fetchone()
            return (row["t"] or None) if row else None

    def run_history(self, repo_id: str, *, mode: str | None = None,
                    limit: int = 100) -> list[dict]:
        """Recent runs for a repo (newest first), live + finished."""
        where = ["repo_id=?"]
        args: list = [repo_id]
        if mode is not None:
            where.append("mode=?")
            args.append(mode)
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM run WHERE {' AND '.join(where)}"
                " ORDER BY datetime(started_at) DESC, id DESC LIMIT ?",
                (*args, int(limit)),
            ).fetchall()
            return [self._run_dict(r) for r in rows]

    # --- item-keyed run helpers (the dev-dashboard work-item run path) ----------
    # Work-item runs (plan, chat) are durable and keyed by (repo_id, item_id) — the daemon
    # tracks them by that pair (not a threaded run_id), mirroring the old in-memory _planning
    # dict it replaces. One running row per item at a time (the per-item run-lock).
    def bump_item_run(self, repo_id: str, item_id: str, *, add_tokens: int = 0,
                      ctx_pct: int | None = None) -> None:
        """Live in-flight estimate for the item's running row (legacy counter + context fill). The
        authoritative per-type accounting is set at finish from the whole-turn usage — see bump_run."""
        sets = ["tokens = tokens + ?"]
        args: list = [int(add_tokens or 0)]
        if ctx_pct is not None:
            sets.append("ctx_pct=?")
            args.append(int(ctx_pct))
        args += [repo_id, item_id]
        with self._conn() as c:
            c.execute(f"UPDATE run SET {','.join(sets)}"
                      " WHERE repo_id=? AND item_id=? AND status='running'", args)

    def set_item_run_tokens(self, repo_id: str, item_id: str, *, tokens: int,
                            ctx_pct: int | None = None) -> None:
        """Set the item's running-row live token estimate ABSOLUTELY (vs bump_item_run's increment).
        Callers dedupe the per-step Usage stream by message_id and pass the running SUM here, so the
        live counter counts each API call once instead of re-adding the several steps a call emits.
        Still just a live estimate — finish_item_run overwrites it with the authoritative Result.usage."""
        sets = ["tokens=?"]
        args: list = [int(tokens or 0)]
        if ctx_pct is not None:
            sets.append("ctx_pct=?")
            args.append(int(ctx_pct))
        args += [repo_id, item_id]
        with self._conn() as c:
            c.execute(f"UPDATE run SET {','.join(sets)}"
                      " WHERE repo_id=? AND item_id=? AND status='running'", args)

    def finish_item_run(self, repo_id: str, item_id: str, *, run_status: str = "done",
                        fallback_tokens: int | None = None,
                        usage: dict | None = None, ctx_pct: int | None = None,
                        outcome: str | None = None, session_id: str | None = None) -> int | None:
        """Close the item's running row (status done|aborted), keeping the accumulated live token
        sum — or `fallback_tokens` if no Usage steps arrived. `usage` (whole-turn final dict) is the
        typed-column fallback (see finish_run). `ctx_pct` is the AUTHORITATIVE end-of-turn context
        fill from the Result — when given it overwrites the last live-bump estimate (mirrors
        finish_run), so an item card shows the true occupancy, not a mid-turn snapshot. `session_id`
        is the run's origin session (from the Result) — attached here so item runs join the
        `session_fate` labeling path (a deleted/retired session stamps its run rows; a NULL session_id
        made that unreachable). Item runs are durable (never purged). Returns the run id, or None if
        nothing was running."""
        run_status = run_status if run_status in _RUN_STATUSES else "done"
        with self._conn() as c:
            row = c.execute(
                "SELECT id, tokens FROM run WHERE repo_id=? AND item_id=? AND status='running'"
                " ORDER BY datetime(started_at) DESC LIMIT 1", (repo_id, item_id),
            ).fetchone()
            if row is None:
                return None
            tokens = row["tokens"] or fallback_tokens or 0
            sets = ["status=?", "ended_at=?", "tokens=?"]
            args: list = [run_status, _now(), int(tokens)]
            if ctx_pct is not None:  # authoritative Result fill overrides the live-bump estimate
                sets.append("ctx_pct=?")
                args.append(int(ctx_pct))
            if outcome:  # a background run's structured completion outcome (S5; validated by caller)
                sets.append("outcome=?")
                args.append(str(outcome))
            if session_id:  # attach origin session so session_fate labeling can reach this row
                sets.append("session_id=?")
                args.append(str(session_id))
            args.append(row["id"])
            c.execute(f"UPDATE run SET {', '.join(sets)} WHERE id=?", args)
            self._finish_usage_apply(c, row["id"], usage)  # authoritative per-type + reconciled tokens
            return row["id"]

    def session_ctx_pct(self, session_id: str | None) -> int | None:
        """The most recent context fill recorded for a session — the reading the compaction
        trigger checks at run START. Every finished run stamps its authoritative end-of-turn
        `ctx_pct` and its origin `session_id`, so the newest such row IS the session's current
        occupancy: nothing has been added to that transcript since. None when the session has no
        finished run yet (a brand-new session cannot be over any trigger)."""
        if not session_id:
            return None
        with self._conn() as c:
            row = c.execute(
                "SELECT ctx_pct FROM run WHERE session_id=? AND ctx_pct IS NOT NULL"
                " AND status!='running' ORDER BY id DESC LIMIT 1", (session_id,),
            ).fetchone()
        return int(row["ctx_pct"]) if row else None

    def session_compacted_pending(self, session_id: str | None) -> str | None:
        """When did this session get compacted, if no real turn has run since? Returns the compact
        run's `ended_at`, else None.

        This is the whole state behind the post-compaction notice — no new column, no in-memory
        flag, and self-clearing: the notice is owed exactly while the NEWEST finished run on the
        session is a `compact` one, and the first real turn afterwards makes it stop being the
        newest. Restart-proof for free, because it is read from the run table."""
        if not session_id:
            return None
        with self._conn() as c:
            row = c.execute(
                "SELECT feature, ended_at FROM run WHERE session_id=? AND status!='running'"
                " ORDER BY id DESC LIMIT 1", (session_id,),
            ).fetchone()
        return (row["ended_at"] if row and row["feature"] == "compact" else None)

    def run_tokens(self, run_id: int | None) -> int:
        """The reconciled `tokens` scalar on a run row — authoritative (3-type, excl. cache_read)
        once the run has finished and its whole-turn usage was applied. Use this over a pre-finish
        `live_run` snapshot, whose `tokens` is the in-flight estimate that OVER-counts (it sums the
        cumulative-for-the-turn Usage snapshots). 0 if the run is unknown."""
        if run_id is None:
            return 0
        with self._conn() as c:
            r = c.execute("SELECT tokens FROM run WHERE id=?", (run_id,)).fetchone()
            return int(r["tokens"]) if r and r["tokens"] is not None else 0

    def live_run(self, repo_id: str, item_id: str) -> dict | None:
        """The item's currently-running row (live time/tokens/model/ctx_pct), or None."""
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM run WHERE repo_id=? AND item_id=? AND status='running'"
                " AND discarded_at IS NULL"
                " ORDER BY datetime(started_at) DESC LIMIT 1", (repo_id, item_id),
            ).fetchone()
            return self._run_dict(r) if r else None

    def is_item_running(self, repo_id: str, item_id: str) -> bool:
        """The per-item run-lock: True iff any run is in flight for this work-item."""
        return self.live_run(repo_id, item_id) is not None

    def running_run_id(self, repo_id: str, item_id: str) -> int | None:
        """The id of the item's in-flight run — the same resolution `log_run_event` does when a
        caller passes only `item_id`. Exposed so the LIVE frame for an event can carry the run it
        was recorded against: a frame published with `run_id: null` cannot be matched against the
        history the browser already holds, and renders a second time on top of it."""
        run = self.live_run(repo_id, item_id)
        return int(run["id"]) if run and run.get("id") is not None else None

    def discard_item_trace(self, repo_id: str, item_id: str, *, at: str) -> dict:
        """SOFT-delete every run + run-event row this work-item has so far — the re-run's "start
        clean" (owner, 2026-07-31). Returns {runs, events} counts.

        NOTHING IS DELETED. The rows keep their tokens, their timings and their tool calls; they
        are stamped `discarded_at` so the ITEM's own surfaces stop showing them while every
        accounting read still counts them ([[never-delete-logs]]). `WHERE discarded_at IS NULL`
        makes it idempotent and keeps each attempt's original stamp across repeated re-runs.

        Call this BEFORE writing the `item.rerun` event, so that event survives unstamped and the
        re-run itself stays on the record even though the attempt it discarded is out of view."""
        with self._conn() as c:
            runs = c.execute(
                "UPDATE run SET discarded_at=? WHERE repo_id=? AND item_id=? AND discarded_at IS NULL",
                (at, repo_id, str(item_id)),
            ).rowcount
            events = c.execute(
                "UPDATE run_event SET discarded_at=? WHERE repo_id=? AND item_id=?"
                " AND discarded_at IS NULL", (at, repo_id, str(item_id)),
            ).rowcount
            c.commit()
        return {"runs": int(runs or 0), "events": int(events or 0)}

    def release_item_runs(self, repo_id: str, item_id: str) -> int:
        """When a work-item is hard-deleted, close out any run still in flight for it (so it can't
        linger as a ghost 'running') but KEEP every run row and its events/artifacts — they are the
        permanent activity + token record and outlive the item (an orphaned item_id is harmless; no
        read path joins a work-item table). Returns the number of live runs closed out.

        (Was delete_item_runs, which wiped the whole trail — the same never-delete-logs violation as
        the old session cascade; see delete_session_record.)"""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE run SET status='aborted', ended_at=? WHERE repo_id=? AND item_id=? AND status='running'",
                (_now(), repo_id, item_id))
            return cur.rowcount

    # `log_artifact` is RETIRED (2026-07-31). It wrote a second, POORER copy of what `log_run_event`
    # already records: no prompt/reply rows, and nothing at all from a deputy or compaction run.
    # Every caller wrote BOTH, so the copy never carried information of its own — and the drilldown's
    # Runs pane happened to read the poor one, showing 17 of one item's 34 runs. The `run_artifact`
    # TABLE and its rows STAY (never-delete-logs): frozen history, no new writes.

    # --- run events (the per-RUN observability trail: prompt · reply · tool/skill/agent calls) ---
    def log_run_event(self, *, repo_id: str, kind: str, name: str, description: str | None = None,
                      run_id: int | None = None, item_id: str | None = None,
                      tool_id: str | None = None, parent_tool_id: str | None = None) -> None:
        """Append one event to a run's trail (`seq` orders within the run). Pass `run_id` directly
        (chat / background), or `item_id` to resolve the item's currently-running run. `tool_id` (the
        SDK tool_use id) lets a `result` row pair back to its call; `parent_tool_id` names the
        sub-agent spawn the row came from (NULL = the parent). Best-effort — never raises."""
        try:
            with self._conn() as c:
                if run_id is None and item_id is not None:
                    r = c.execute(
                        "SELECT id FROM run WHERE repo_id=? AND item_id=? AND status='running'"
                        " ORDER BY datetime(started_at) DESC LIMIT 1", (repo_id, item_id),
                    ).fetchone()
                    run_id = r["id"] if r else None
                if run_id is None:
                    return  # no run to attach to — drop rather than orphan
                seq = c.execute(
                    "SELECT COALESCE(MAX(seq),0)+1 AS n FROM run_event WHERE run_id=?", (run_id,),
                ).fetchone()["n"]
                c.execute(
                    "INSERT INTO run_event"
                    " (run_id,repo_id,item_id,seq,kind,name,description,tool_id,parent_tool_id,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, repo_id, item_id, seq, kind, name, description, tool_id,
                     parent_tool_id, _now()),
                )
        except Exception:  # noqa: BLE001 — telemetry must never break a turn
            pass

    def record_run_input(self, run_id: int, *, repo_id: str, item_id: str | None, phase: str | None,
                         feature: str | None, background: bool, system_prompt: str,
                         prompt_body: str, system_fragments: str | None = None,
                         turn_surface: str | None = None,
                         authored_extras: str | None = None) -> None:
        """Persist the ACTUAL full input a run sent (prompt inspector "A"), keyed by run_id. One row
        per run (INSERT OR REPLACE — a re-run under the same id overwrites). `system_fragments` is the
        JSON provenance breakdown of `system_prompt` (ordered [{name,location,text}]); `turn_surface`
        is the JSON record of what the turn was ALLOWED to do (tools, write boundary, sandbox, model,
        effort); `authored_extras` is the JSON {skills, tools} of the OTHER prompt text SuperMe wrote
        into this run (the phase skill body + the mounted tools' own docs). All optional.
        Best-effort telemetry — never breaks a turn."""
        try:
            with self._conn() as c:
                c.execute(
                    "INSERT OR REPLACE INTO run_input"
                    " (run_id,repo_id,item_id,phase,feature,background,system_prompt,prompt_body,"
                    "system_fragments,turn_surface,authored_extras,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (int(run_id), repo_id, item_id, phase, feature, 1 if background else 0,
                     system_prompt, prompt_body, system_fragments, turn_surface, authored_extras,
                     _now()),
                )
        except Exception:  # noqa: BLE001 — telemetry must never break a turn
            pass

    def read_run_input(self, run_id: int) -> dict | None:
        """The captured input for one run (or None), for the inspector's "A" page."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM run_input WHERE run_id=?", (int(run_id),)).fetchone()
            return dict(row) if row else None

    def set_run_feature(self, run_id: int, feature: str) -> None:
        """Stamp a run's `feature` tag (used to mark a throwaway prompt-extraction run so its KEPT
        trace + token spend bucket under 'prompt-extraction'). Best-effort telemetry."""
        try:
            with self._conn() as c:
                c.execute("UPDATE run SET feature=? WHERE id=?", (feature, int(run_id)))
        except Exception:  # noqa: BLE001 — telemetry must never break a turn
            pass

    def list_run_inputs_for_item(self, item_id: str) -> list[dict]:
        """Every captured-input row for an item (prompt inspector "A"), oldest first — the per-phase
        run list the Prompt X-ray tab turns into `input.html` links. Survives the throwaway item's
        folder deletion (run_input rows are kept trace, keyed by the dangling item_id)."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT ri.run_id, ri.phase, ri.feature, ri.created_at, r.started_at, r.status"
                " FROM run_input ri LEFT JOIN run r ON r.id = ri.run_id"
                " WHERE ri.item_id=? ORDER BY ri.run_id ASC", (str(item_id),)).fetchall()
            return [dict(r) for r in rows]

    def get_prompt_extraction_state(self, repo_id: str) -> dict | None:
        """The repo's latest Prompt X-ray probe state (or None) — {item_id, status, started_at,
        finished_at}. A durable per-repo pointer (survives the throwaway item's teardown) so the tab
        can list the captured A-links for the last probe even after its folder is gone."""
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting WHERE key=?",
                          (f"prompt_extraction:{repo_id}",)).fetchone()
        if r is None or r["value"] is None:
            return None
        try:
            return json.loads(r["value"])
        except (ValueError, TypeError):
            return None

    def set_prompt_extraction_state(self, repo_id: str, state: dict) -> None:
        """Persist the repo's latest Prompt X-ray probe state (see get_prompt_extraction_state)."""
        with self._conn() as c:
            c.execute(
                "INSERT INTO system_setting (key,value,updated_at) VALUES (?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (f"prompt_extraction:{repo_id}", json.dumps(state), _now()),
            )

    def get_run(self, run_id: int) -> dict | None:
        """One run row by id (or None) — the single-run read behind the diagnosis/inspection tool."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM run WHERE id=?", (int(run_id),)).fetchone()
            return self._run_dict(row) if row else None

    def events_for_run(self, run_id: int) -> list[dict]:
        """The full per-run trail (prompt · replies · calls), in order — powers the Activity trace."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, seq, kind, name, description, tool_id, parent_tool_id, created_at"
                " FROM run_event WHERE run_id=? ORDER BY seq ASC", (int(run_id),),
            ).fetchall()
            return [dict(r) for r in rows]

    def events_for_item(self, repo_id: str, item_id: str) -> list[dict]:
        """Every trail row an item's runs recorded, oldest-first within each run, newest run first —
        the feed behind the drilldown's Runs pane and the `execution.md` snapshot.

        Replaced `artifacts_for_item`, which read `run_artifact`: that table was written only by the
        item-run path, so a deputy run (20+ real calls) or a compaction run produced no rows there
        and the pane silently skipped it — 17 of one item's 34 runs were missing, including every
        deputy judgment. `run_event` is a strict superset, item-tagged on every row."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, run_id, seq, kind, name, description, tool_id, parent_tool_id, created_at"
                " FROM run_event WHERE repo_id=? AND item_id=? AND discarded_at IS NULL"
                " ORDER BY run_id IS NULL, run_id DESC, seq ASC", (repo_id, item_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def run_stats(self, repo_id: str, *, mode: str | None = None) -> dict[str, dict]:
        """Per-item accumulated telemetry over FINISHED item runs (the card totals): {item_id:
        {total_tokens, runs, last_tokens, last_duration_ms, last_model, last_ctx_pct}}.
        Running rows are excluded (their live figures come from live_run).

        Discarded runs are excluded too: this is what the card and the drilldown header print, and
        an item that reads "Σ 1.6M tok" over work it no longer contains is the exact confusion the
        soft delete exists to end. The repo and system totals still count them — see the schema
        note on `run.discarded_at`."""
        where = ["repo_id=?", "status!='running'", "item_id IS NOT NULL", "discarded_at IS NULL"]
        args: list = [repo_id]
        if mode is not None:
            where.append("mode=?")
            args.append(mode)
        out: dict[str, dict] = {}
        with self._conn() as c:
            rows = c.execute(
                f"SELECT item_id, tokens, tok_input, tok_cache_creation, tok_cache_read, tok_output,"
                f" model, ctx_pct, phase, started_at, ended_at FROM run"
                f" WHERE {' AND '.join(where)} ORDER BY datetime(started_at)", args,
            ).fetchall()
        for r in rows:
            s = out.setdefault(r["item_id"], {"total_tokens": 0, "runs": 0, "last_tokens": 0,
                                              "last_duration_ms": None, "last_model": None,
                                              "last_ctx_pct": None, "last_ended_at": None,
                                              "by_phase": {}, "by_phase_cr": {}})
            toks = self._display_tokens(r)  # 3-type (excl cache_read), matches the dashboard default
            s["total_tokens"] += toks
            s["runs"] += 1
            # Per-phase accumulation, BOTH bases: `by_phase` = 3-type (what the card shows), `by_phase_cr`
            # = cache_read (so 4-type-per-phase = by_phase + by_phase_cr is recorded behind it). A run's
            # tokens land in the phase it ran in (Stage D); pre-phase-column runs bucket under "unknown".
            ph = r["phase"] or "unknown"
            s["by_phase"][ph] = s["by_phase"].get(ph, 0) + toks
            s["by_phase_cr"][ph] = s["by_phase_cr"].get(ph, 0) + (r["tok_cache_read"] or 0)
            s["last_tokens"] = toks
            s["last_duration_ms"] = _duration_ms(r["started_at"], r["ended_at"])
            s["last_model"] = r["model"]
            s["last_ctx_pct"] = r["ctx_pct"]
            # WHEN the item last actually did something — the card's "3 minutes ago". The rows are
            # ordered by `started_at`, so the final assignment is the newest finished run. It is the
            # END, not the start: an item whose 40-minute run finished a minute ago has just moved,
            # and dating it by the start would read as forty minutes idle.
            s["last_ended_at"] = r["ended_at"]
        return out

    def recent_runs(self, *, limit: int = 50) -> list[dict]:
        """The most recent runs across ALL repos (newest first) — the system-wide run log."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM run ORDER BY datetime(started_at) DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [self._run_dict(r) for r in rows]

    def token_usage(self) -> dict:
        """System-wide token aggregation with FULL visibility (token-usage-tracking-spec).

        Every token is attributable along TWO axes that reconcile to the same total by construction
        (same rows, same columns, summed two ways):
          • Breakdown 1 — SEMANTIC (`by_category` → features): what tokens were spent on.
          • Breakdown 2 — SYSTEMATIC (`by_type`): input / cache_creation / cache_read / output.

        Per row, the accounted amount = input + cache_creation + output (3-type, EXCLUDES cache_read).
        A row whose four typed columns are all zero contributes NOTHING: it never returned a final
        usage (aborted, killed, errored), so its `tokens` scalar is the live in-flight estimate rather
        than a measurement, and this table carries measured usage only (2026-08-11 — the `legacy`
        bucket that used to fold it in is gone). `total` and every by_* bucket sum this 3-type amount — cache_read is kept in `by_type`
        (never lost) so the full 4-type volume = `total + by_type["cache_read"]` (the dashboard toggle).
        Sums EVERY run row, so in-flight spend is included. SuperMe-context only. Back-compat:
        `total`/`by_scope`/`by_feature` are retained. (v1 caveat: forge-eval spend isn't in the spine.)"""
        from .token_taxonomy import category_for, CATEGORY_ORDER
        with self._conn() as c:
            rows = c.execute(
                "SELECT repo_id, mode, feature, COUNT(*) AS n,"
                " COALESCE(SUM(tok_input),0) AS ti, COALESCE(SUM(tok_cache_creation),0) AS tcc,"
                " COALESCE(SUM(tok_cache_read),0) AS tcr, COALESCE(SUM(tok_output),0) AS to_"
                " FROM run GROUP BY repo_id, mode, feature"
            ).fetchall()

        def _blank_type() -> dict:
            return {"input": 0, "cache_creation": 0, "cache_read": 0, "output": 0}

        def _add_type(d: dict, r) -> int:
            # by_type accumulates ALL four components (cache_read included, so it stays inspectable);
            # the RETURNED accounted amount is 3-type (EXCLUDES cache_read) — that's what `total` and
            # every by_* bucket sum, so the dashboard default + activity + work-item all read 3-type.
            # The full 4-type volume = total + by_type["cache_read"] (the dashboard toggle adds it).
            d["input"] += r["ti"]; d["cache_creation"] += r["tcc"]
            d["cache_read"] += r["tcr"]; d["output"] += r["to_"]
            return r["ti"] + r["tcc"] + r["to_"]

        total = 0
        by_scope: dict[str, int] = {}
        by_feature: dict[str, int] = {}
        by_feature_cr: dict[str, int] = {}  # per-feature cache_read, so "By operation" can render 4-type
        by_type = _blank_type()
        by_category: dict[str, dict] = {}
        by_repo: dict[str, dict] = {}
        for r in rows:
            amt = _add_type(by_type, r)  # the row-group's accounted (3-type) amount
            total += amt
            by_scope[r["mode"]] = by_scope.get(r["mode"], 0) + amt
            by_feature[r["feature"]] = by_feature.get(r["feature"], 0) + amt
            by_feature_cr[r["feature"]] = by_feature_cr.get(r["feature"], 0) + r["tcr"]
            cat = category_for(r["feature"])
            cnode = by_category.setdefault(cat, {"total": 0, "features": {}})
            cnode["total"] += amt
            cnode["features"][r["feature"]] = cnode["features"].get(r["feature"], 0) + amt
            pr = by_repo.setdefault(r["repo_id"], {
                "total": 0, "runs": 0, "by_scope": {}, "by_feature": {},
                "by_type": _blank_type(), "by_category": {},
            })
            _add_type(pr["by_type"], r)
            pr["total"] += amt
            pr["runs"] += r["n"] or 0
            pr["by_scope"][r["mode"]] = pr["by_scope"].get(r["mode"], 0) + amt
            pr["by_feature"][r["feature"]] = pr["by_feature"].get(r["feature"], 0) + amt
            pcat = pr["by_category"].setdefault(cat, {"total": 0, "features": {}})
            pcat["total"] += amt
            pcat["features"][r["feature"]] = pcat["features"].get(r["feature"], 0) + amt

        # Stable category ordering (known first, catch-all last) for both global + per-repo trees.
        def _order(tree: dict) -> dict:
            return {k: tree[k] for k in CATEGORY_ORDER if k in tree}

        by_category = _order(by_category)
        for pr in by_repo.values():
            pr["by_category"] = _order(pr["by_category"])

        # "Old projects" — spend whose repo is no longer connected. Its runs are preserved forever,
        # so it stays in `total`; without this bucket it would be counted-but-unnameable (the orbit
        # renders the LIVE roster, so the visible nodes wouldn't sum to the header). Every repo_id
        # missing from the roster lands here, whether it left through disconnect (tombstoned, so we
        # know its label) or predates tombstoning (falls back to the bare id).
        live, tombs = self.repos(), self.archived_repos()
        members = [
            {"id": rid, "label": (tombs.get(rid) or {}).get("label") or rid,
             "total": pr["total"], "runs": pr["runs"],
             "disconnected_at": (tombs.get(rid) or {}).get("disconnected_at")}
            for rid, pr in by_repo.items() if rid not in live
        ]
        members.sort(key=lambda m: m["total"], reverse=True)
        archived = {"total": sum(m["total"] for m in members),
                    "runs": sum(m["runs"] for m in members), "repos": members}
        return {
            "global": {
                "total": total,
                "by_scope": by_scope,
                "by_feature": by_feature,
                "by_feature_cache_read": by_feature_cr,
                "by_type": by_type,
                "by_category": by_category,
            },
            "by_repo": by_repo,
            "archived": archived,
        }

    def token_timeseries(self, tz_offset: int = 0) -> dict:
        """Per-day token usage for the trend graph (token-usage-tracking-spec, decision 7).

        Buckets by LOCAL day: `started_at` is stored UTC, and `tz_offset` (minutes to ADD to UTC to
        reach the owner's local time, e.g. +540 for JST — the FE sends `-getTimezoneOffset()`) shifts
        it so "which day" matches the owner's day, not UTC. Each day carries the four token types and a
        `total`; `cumulative` is the running total of daily totals, so the
        same series renders as per-day bars, a cumulative line, or a stacked breakdown. Derived on the
        fly from the durable run rows — no materialized rollup."""
        modifier = f"{int(tz_offset):+d} minutes"
        with self._conn() as c:
            rows = c.execute(
                "SELECT date(started_at, ?) AS day, COUNT(*) AS n,"
                " COALESCE(SUM(tok_input),0) AS ti, COALESCE(SUM(tok_cache_creation),0) AS tcc,"
                " COALESCE(SUM(tok_cache_read),0) AS tcr, COALESCE(SUM(tok_output),0) AS to_"
                " FROM run WHERE started_at IS NOT NULL GROUP BY day ORDER BY day",
                (modifier,),
            ).fetchall()
        by_day = {r["day"]: r for r in rows if r["day"]}
        days: list[dict] = []
        cumulative = 0
        if by_day:
            # Emit a CONTIGUOUS day axis: walk every calendar day from the first to the last with data
            # and fill gaps with a zero-day, so days with no runs aren't silently skipped (the bars
            # would otherwise mis-space and the date axis lie). Cumulative stays flat across zero-days.
            start, end = date.fromisoformat(min(by_day)), date.fromisoformat(max(by_day))
            d = start
            while d <= end:
                key = d.isoformat()
                r = by_day.get(key)
                ti, tcc, tcr, to_, n = (
                    (r["ti"], r["tcc"], r["tcr"], r["to_"], r["n"] or 0) if r else (0, 0, 0, 0, 0)
                )
                # `total` is 3-type (EXCLUDES cache_read) to match the dashboard default; cache_read is
                # still carried as its own field so the stacked/toggle view can show the full volume.
                #
                # A run whose four typed columns are all zero contributes NOTHING here, by design
                # (2026-08-11). Those are runs that never returned a final usage — aborted, killed or
                # errored — and the `tokens` scalar they carry is the live in-flight estimate, not a
                # measurement. There used to be a fifth `legacy` series folding that estimate in; it
                # broke the one rule this table has (measured usage only, unmeasured is 0, never
                # another metric filed into the same column) and it read as unmigrated debt when it
                # was really "the daemon restarted mid-run". The rows themselves are untouched — the
                # trail keeps them, the budget breaker still counts them; they are simply not a
                # token TYPE and so have no place on a token-type axis.
                total = ti + tcc + to_
                cumulative += total
                days.append({
                    "day": key,
                    "input": ti, "cache_creation": tcc, "cache_read": tcr, "output": to_,
                    "total": total, "cumulative": cumulative, "runs": n,
                })
                d += timedelta(days=1)
        return {"days": days, "total": cumulative}

    # --- computed repo live-status (NOT stored — derived from runs) -------------
    def repo_status(self, repo_id: str, mode: str) -> dict:
        """A repo×scope's live status, computed from runs: active iff a run is in flight;
        last_activity = the most recent run's timestamp; current_item = a running run's item."""
        with self._conn() as c:
            running = c.execute(
                "SELECT item_id FROM run WHERE repo_id=? AND mode=? AND status='running'"
                " ORDER BY datetime(started_at) DESC LIMIT 1", (repo_id, mode),
            ).fetchone()
            last = c.execute(
                "SELECT COALESCE(ended_at, started_at) AS ts FROM run"
                " WHERE repo_id=? AND mode=? ORDER BY datetime(COALESCE(ended_at, started_at)) DESC LIMIT 1",
                (repo_id, mode),
            ).fetchone()
        return {
            "active": running is not None,
            "current_item": running["item_id"] if running else None,
            "last_activity": last["ts"] if last else None,
        }

    # --- counts (for repo×scope summaries on the monitor dashboard) -------------
    def session_count(self, repo_id: str, mode: str | None = None, *,
                      include_agent_threads: bool = False) -> int:
        """How many sessions this repo (× mode) has. Counts CONVERSATIONS by default — the threads
        the owner can open and take a turn in — so the number agrees with the session picker.
        `include_agent_threads` adds the headless build/vet threads (kind_profiles.AGENT_THREAD_KINDS)
        for a genuine total. A NULL kind is a legacy pre-roles session: a conversation."""
        where = ["repo_id=?"]
        args: list = [repo_id]
        if mode is not None:
            where.append("mode=?")
            args.append(mode)
        if not include_agent_threads and AGENT_THREAD_KINDS:
            where.append(f"(kind IS NULL OR kind NOT IN ({','.join('?' * len(AGENT_THREAD_KINDS))}))")
            args.extend(AGENT_THREAD_KINDS)
        with self._conn() as c:
            return c.execute(f"SELECT COUNT(*) FROM session WHERE {' AND '.join(where)}",
                             args).fetchone()[0]

    def run_count(self, repo_id: str, mode: str | None = None) -> int:
        where = ["repo_id=?"]
        args: list = [repo_id]
        if mode is not None:
            where.append("mode=?")
            args.append(mode)
        with self._conn() as c:
            return c.execute(f"SELECT COUNT(*) FROM run WHERE {' AND '.join(where)}",
                             args).fetchone()[0]

    def live_agent_runs(self, repo_id: str, mode: str) -> dict:
        """The RUN-based half of the agent metric (the "running now" pieces): how many work-items
        are executing a turn right now, and how many learning jobs (distill/sweep) are running.

        It deliberately does NOT count idle in_progress work-items — those have no live run, so a
        live-but-paused item (e.g. plan done, awaiting the owner) must be counted from the work-item
        STORE by status, not from here. The caller combines: agents = active-items-by-status +
        learn_running; running = items_running + learn_running."""
        learn = tuple(sorted(LEARNING_FEATURES))
        qmarks = ",".join("?" * len(learn))
        with self._conn() as c:
            items_running = c.execute(
                "SELECT COUNT(DISTINCT item_id) FROM run WHERE repo_id=? AND mode=? "
                "AND item_id IS NOT NULL AND status='running'",
                (repo_id, mode)).fetchone()[0]
            learn_running = c.execute(
                f"SELECT COUNT(*) FROM run WHERE repo_id=? AND mode=? AND item_id IS NULL "
                f"AND status='running' AND feature IN ({qmarks})",
                (repo_id, mode, *learn)).fetchone()[0]
            # Onboarding (project-init/retrofit) is a workflow agent too (session-agent-lifecycle-prd):
            # an unestablished repo's general dev turn runs as feature='onboarding'. Count it running.
            onboarding_running = c.execute(
                "SELECT COUNT(*) FROM run WHERE repo_id=? AND mode=? AND item_id IS NULL "
                "AND status='running' AND feature='onboarding'",
                (repo_id, mode)).fetchone()[0]
        return {"items_running": items_running, "learn_running": learn_running,
                "onboarding_running": onboarding_running}

    # --- model overrides --------------------------------------------------------
    def get_model_override(self, repo_id: str) -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT model FROM model_override WHERE repo_id=?", (repo_id,)).fetchone()
            return r["model"] if r else None

    def set_model_override(self, repo_id: str, model: str | None) -> None:
        """Set (or clear, with model=None) a repo's runtime model preference."""
        with self._conn() as c:
            if model is None:
                c.execute("DELETE FROM model_override WHERE repo_id=?", (repo_id,))
            else:
                c.execute(
                    "INSERT INTO model_override (repo_id,model,updated_at) VALUES (?,?,?)"
                    " ON CONFLICT(repo_id) DO UPDATE SET model=excluded.model, updated_at=excluded.updated_at",
                    (repo_id, model, _now()),
                )

    def all_model_overrides(self) -> dict[str, str]:
        with self._conn() as c:
            return {r["repo_id"]: r["model"]
                    for r in c.execute("SELECT repo_id, model FROM model_override").fetchall()}

    # --- system-wide model default (the floor below per-repo overrides) ----------
    def get_system_model(self) -> str | None:
        """The runtime-set system default model (None = fall back to the YAML/host default)."""
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting WHERE key='default_model'").fetchone()
            return r["value"] if r and r["value"] else None

    def set_system_model(self, model: str | None) -> None:
        """Set (or clear, with model=None) the system-wide default model override."""
        with self._conn() as c:
            if model is None:
                c.execute("DELETE FROM system_setting WHERE key='default_model'")
            else:
                c.execute(
                    "INSERT INTO system_setting (key,value,updated_at) VALUES ('default_model',?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (model, _now()),
                )

    def effective_system_model(self) -> str:
        """The system default model the resolver should use — ALWAYS a concrete, known id (never the
        opaque host/CLI default): the runtime override, else the static `config/system.yaml` default,
        else the built-in floor. Any tier alias is resolved to its latest concrete id."""
        from .models import DEFAULT_MODEL, normalize_model
        return normalize_model(self.get_system_model() or self.system_config().default_model) or DEFAULT_MODEL

    def effective_model(self, repo_id: str, *, per_call: str | None = None,
                        item_model: str | None = None) -> str:
        """THE model-precedence resolver — the ONE choke every interactive/plan run resolves through
        so the tiers can't drift apart (session-model-precedence). Most-specific first:
          per_call (an explicit per-turn/session pick — the surface's runtime override)
            → item_model (a work-item's configured model, for its bound runs)
            → this repo's persisted default (`model_override[repo_id]`, set only by repo config)
            → the system default (which itself floors to DEFAULT_MODEL, so this never returns None).
        Aliases are kept — the concrete latest is resolved at consumption (agent_service normalizes),
        so a pick auto-tracks a MODEL_TIERS bump. `per_call` is the session tier: the surface holds it
        (e.g. the chat's per-session model) and passes it in; it NEVER writes the repo default."""
        return (per_call or item_model or self.get_model_override(repo_id)
                or self.effective_system_model())

    # --- per-agent model (the autonomous background sub-agents; owner-tunable) ------------------
    # SOURCE OF TRUTH = each sub-agent's own `.md` frontmatter `model:` field (two-way sync with the
    # config UI). The daemon's orchestrator turn reads the SAME value via resolve_agent_model(), so
    # the trigger turn and the sub-agent always run the same model. Falls back to the code preset
    # (core.models.AGENT_MODELS) only if the file is missing or has no model set.
    @staticmethod
    def _agent_md_path(feature: str):
        from .models import AGENT_MD_NAME
        from ..runtime.config import DEV_PLUGIN_DIR
        name = AGENT_MD_NAME.get(feature)
        return (DEV_PLUGIN_DIR / "agents" / f"{name}.md") if name else None

    def _agent_file_model(self, feature: str) -> str | None:
        """The raw `model:` from the sub-agent's `.md` frontmatter (None if absent/unreadable)."""
        from .operational import parse_frontmatter
        path = self._agent_md_path(feature)
        if not path or not path.is_file():
            return None
        try:
            meta, _ = parse_frontmatter(path.read_text())
        except Exception:
            return None
        return meta.get("model") or None

    def resolve_agent_model(self, feature: str) -> str:
        """The concrete, latest model a background sub-agent should run on: its `.md` frontmatter
        tier alias resolved to its tier's CURRENT concrete id (auto-tracks MODEL_TIERS) → else the code
        preset. Never None. This is the consumption point — the runners pass it to run_turn, so the
        alias on disk becomes the latest concrete here (never the lagging CLI alias)."""
        from .models import agent_model, track_to_latest
        return track_to_latest(self._agent_file_model(feature)) or agent_model(feature)

    def set_agent_model(self, feature: str, model: str | None) -> None:
        """Write a sub-agent's model into its `.md` frontmatter (the source of truth), stored as a TIER
        ALIAS (`sonnet`/`opus`/`haiku`) — the canonical on-disk form everywhere. SuperMe resolves the
        alias to the latest concrete id at CONSUMPTION (resolve_agent_model → run_turn's normalize), so
        the file stays version-agnostic and a MODEL_TIERS bump needs no file rewrite. Accepts an alias
        or a concrete id (the tier is derived); model=None falls back to the preset tier."""
        from .models import AGENT_MODELS, model_family
        from .operational import set_frontmatter_field
        path = self._agent_md_path(feature)
        if not path or not path.is_file():
            raise ValueError(f"no agent .md for feature '{feature}'")
        alias = model_family(model) or AGENT_MODELS.get(feature) or "sonnet"
        set_frontmatter_field(path, "model", alias)

    _AGENT_EFFORT_DEFAULT = "medium"
    _AGENT_EFFORTS = ("low", "medium", "high")

    def _agent_file_effort(self, feature: str) -> str | None:
        """The raw `effort:` from the sub-agent's `.md` frontmatter (None if absent/unreadable)."""
        from .operational import parse_frontmatter
        path = self._agent_md_path(feature)
        if not path or not path.is_file():
            return None
        try:
            meta, _ = parse_frontmatter(path.read_text())
        except Exception:
            return None
        return meta.get("effort") or None

    def resolve_agent_effort(self, feature: str) -> str:
        """The reasoning effort a background sub-agent runs at: its `.md` `effort:` field → else the
        'medium' default. The runners pass this to the SDK turn (so it's not the opaque default)."""
        eff = (self._agent_file_effort(feature) or "").strip().lower()
        return eff if eff in self._AGENT_EFFORTS else self._AGENT_EFFORT_DEFAULT

    def set_agent_effort(self, feature: str, effort: str | None) -> None:
        """Write a sub-agent's reasoning effort into its `.md` frontmatter (source of truth)."""
        from .operational import set_frontmatter_field
        path = self._agent_md_path(feature)
        if not path or not path.is_file():
            raise ValueError(f"no agent .md for feature '{feature}'")
        eff = (effort or "").strip().lower()
        set_frontmatter_field(path, "effort", eff if eff in self._AGENT_EFFORTS else self._AGENT_EFFORT_DEFAULT)

    def reconcile_model_overrides(self) -> None:
        """Normalize the picker overrides (system default + every per-repo/context override) to their
        TIER ALIAS (`sonnet`) — the canonical DB form, matching the agent-model files. Migrates any
        legacy concrete id (`claude-sonnet-5`) back to its alias so old picks auto-track a MODEL_TIERS
        bump. Idempotent; run at daemon startup alongside reconcile_agent_models()."""
        from .models import model_family
        with self._conn() as c:
            row = c.execute("SELECT value FROM system_setting WHERE key='default_model'").fetchone()
            if row and row["value"]:
                alias = model_family(row["value"])
                if alias and alias != row["value"]:
                    c.execute("UPDATE system_setting SET value=?, updated_at=? WHERE key='default_model'",
                              (alias, _now()))
            for r in c.execute("SELECT repo_id, model FROM model_override").fetchall():
                alias = model_family(r["model"])
                if alias and alias != r["model"]:
                    c.execute("UPDATE model_override SET model=?, updated_at=? WHERE repo_id=?",
                              (alias, _now(), r["repo_id"]))

    def reconcile_agent_models(self) -> None:
        """Normalize every learning sub-agent's `.md` model to its TIER ALIAS (`sonnet`) — the canonical
        on-disk form. Consumption resolves the concrete latest via resolve_agent_model(), so files never
        carry a concrete id. Idempotent; migrates any legacy concrete id back to its alias. Run at daemon
        startup."""
        from .models import AGENT_MODEL_FEATURES, model_family
        from .operational import set_frontmatter_field
        for feat in AGENT_MODEL_FEATURES:
            cur = self._agent_file_model(feat)
            alias = model_family(cur)
            path = self._agent_md_path(feat)
            if alias and cur and alias != cur and path and path.is_file():
                set_frontmatter_field(path, "model", alias)

    def agent_model_config(self) -> list[dict]:
        """The tunable background sub-agents, in display order: for each, its label, scope, the tier
        it tracks (`sonnet`/`opus`/`haiku`), and the concrete model that tier currently resolves to."""
        from .models import (AGENT_MODEL_FEATURES, AGENT_MODEL_LABELS, AGENT_MODEL_SCOPE,
                             agent_model, track_to_latest, model_family)
        out: list[dict] = []
        for feat in AGENT_MODEL_FEATURES:
            model = track_to_latest(self._agent_file_model(feat)) or agent_model(feat)
            out.append({
                "feature": feat,
                "label": AGENT_MODEL_LABELS.get(feat, feat.title()),
                "scope": AGENT_MODEL_SCOPE,
                "tier": model_family(model) or "",
                "model": model,
                "effort": self.resolve_agent_effort(feat),
            })
        return out

    # --- reasoning effort (mirrors model: per-repo override + system default, floor "medium") ----
    DEFAULT_EFFORT = "medium"

    def get_effort_override(self, repo_id: str) -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT effort FROM effort_override WHERE repo_id=?", (repo_id,)).fetchone()
            return r["effort"] if r else None

    def set_effort_override(self, repo_id: str, effort: str | None) -> None:
        """Set (or clear, with effort=None) a repo's runtime reasoning-effort preference."""
        with self._conn() as c:
            if effort is None:
                c.execute("DELETE FROM effort_override WHERE repo_id=?", (repo_id,))
            else:
                c.execute(
                    "INSERT INTO effort_override (repo_id,effort,updated_at) VALUES (?,?,?)"
                    " ON CONFLICT(repo_id) DO UPDATE SET effort=excluded.effort, updated_at=excluded.updated_at",
                    (repo_id, effort, _now()),
                )

    def all_effort_overrides(self) -> dict[str, str]:
        with self._conn() as c:
            return {r["repo_id"]: r["effort"]
                    for r in c.execute("SELECT repo_id, effort FROM effort_override").fetchall()}

    def get_system_effort(self) -> str | None:
        """The runtime-set system default effort (None = fall back to the YAML default)."""
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting WHERE key='default_effort'").fetchone()
            return r["value"] if r and r["value"] else None

    def set_system_effort(self, effort: str | None) -> None:
        """Set (or clear, with effort=None) the system-wide default reasoning effort."""
        with self._conn() as c:
            if effort is None:
                c.execute("DELETE FROM system_setting WHERE key='default_effort'")
            else:
                c.execute(
                    "INSERT INTO system_setting (key,value,updated_at) VALUES ('default_effort',?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (effort, _now()),
                )

    def effective_system_effort(self) -> str:
        """System default effort: runtime override → YAML default → the built-in 'medium' floor."""
        return self.get_system_effort() or self.system_config().default_effort or self.DEFAULT_EFFORT

    def effective_effort(self, repo_id: str, *, per_call: str | None = None,
                         item_effort: str | None = None) -> str:
        """The effort a turn should use — mirrors effective_model's precedence, most-specific first:
        per_call (per-turn/session pick) → item_effort (a work-item's configured effort) → this
        repo's override → the system default (which floors to 'medium'). Never None. Callers with no
        session/item context just pass repo_id (→ repo → system → floor)."""
        return (per_call or item_effort or self.get_effort_override(repo_id)
                or self.effective_system_effort())

    # --- build⟷vet loop (build-vet-loop §5) ---------------------------------------
    # Token budget = the PRIMARY breaker (§5.2): the loop stops when an item's build+vet spend
    # crosses it — measured, not proxied by a cycle count. Precedence (§8·O2, v1): the item's own
    # `loop_budget` frontmatter → the system default below. (A per-repo tier mirrors model/effort
    # when a real need shows; noted in the design doc.) Default is calibrated to the one measured
    # reference: 294k for a mostly-manual pass on test-playground → ~1.7× headroom.
    DEFAULT_LOOP_BUDGET = 500_000

    def item_phase_tokens(self, repo_id: str, item_id: str,
                          phases: tuple[str, ...] = ("build", "vet")) -> int:
        """An item's accumulated 3-type token spend over the given phases (every run — live and
        finished, interactive and background: the loop's meter reads what the item actually cost in
        those phases, whoever spent it). Legacy pre-typed rows fall back to `tokens`.

        DISCARDED RUNS ARE EXCLUDED, and this is the reason the filter matters most: this feeds the
        loop's BUDGET BREAKER. Counting a re-run's thrown-away attempt would hand the fresh attempt
        a budget that is already spent, and it would die on its first vet with `budget` — the exact
        failure the plan-revision generation window was invented to work around."""
        ph = ",".join("?" for _ in phases)
        with self._conn() as c:
            r = c.execute(
                f"SELECT COALESCE(SUM(CASE WHEN (tok_input+tok_cache_creation+tok_cache_read+tok_output)=0"
                f" THEN COALESCE(tokens,0) ELSE tok_input+tok_cache_creation+tok_output END),0) AS t"
                f" FROM run WHERE repo_id=? AND item_id=? AND discarded_at IS NULL"
                f" AND phase IN ({ph})",
                (repo_id, item_id, *phases),
            ).fetchone()
            return int(r["t"] or 0)

    def get_loop_budget(self) -> int:
        """The system-default loop token budget (runtime-set → the built-in default)."""
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting WHERE key='loop_token_budget'").fetchone()
        try:
            return int(str(r["value"]).strip()) if r and r["value"] else self.DEFAULT_LOOP_BUDGET
        except (TypeError, ValueError):
            return self.DEFAULT_LOOP_BUDGET

    def set_loop_budget(self, budget: int | None) -> None:
        """Set (or clear, with None) the system-default loop token budget."""
        with self._conn() as c:
            if budget is None:
                c.execute("DELETE FROM system_setting WHERE key='loop_token_budget'")
            else:
                c.execute(
                    "INSERT INTO system_setting (key,value,updated_at) VALUES ('loop_token_budget',?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (str(int(budget)), _now()),
                )

    def effective_loop_budget(self, repo_id: str, item_budget=None) -> int:
        """The budget the loop breaker measures against for one item: the item's own
        `loop_budget` (frontmatter, string-coerced) → the system default."""
        try:
            if item_budget is not None and str(item_budget).strip():
                return int(str(item_budget).strip())
        except (TypeError, ValueError):
            pass
        return self.get_loop_budget()

    # `loop_autorun` (get/set) is RETIRED (owner, 2026-07-30). It degraded the build⟷vet loop to
    # decide-and-page at every hop — but that stretch is human-free by contract, so the switch had
    # no case it served: it rested items at `awaiting_human` INSIDE the loop, indistinguishable
    # from a real fault, in a phase with no decision surface. It also never had a route or a UI
    # (only a test ever called the setter), so it was unreachable in practice. The token budget
    # remains the loop's ceiling.

    # --- delegated deputy authority (BV-A2.2) ------------------------------------
    # The authorization scopes the deputy may GRANT on its own at the review gate. The line (build-
    # vet-autonomy-design.md): the deputy may authorize changes that SYNC the contract to shipped
    # reality; the owner reserves changes that DEFINE or alter intent. The default is that sync set,
    # widenable/narrowable per-system. Everything NOT in the set escalates — the human floor holds.
    # (The scope vocabulary itself is artifacts.AUTH_SCOPES; this stores a subset. Kept literal here
    # so a spine read needs no cross-module import.)
    DEFAULT_DELEGATED_AUTHORITY = ("doc-sync", "rename-to-shipped", "roadmap-mark-done")

    def get_deputy_delegated_authority(self) -> list[str]:
        """The scopes the deputy may grant unaided (default = the sync-to-reality set)."""
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting "
                          "WHERE key='deputy_delegated_authority'").fetchone()
        if r is None or r["value"] is None:
            return list(self.DEFAULT_DELEGATED_AUTHORITY)
        return [s.strip() for s in str(r["value"]).split(",") if s.strip()]

    def set_deputy_delegated_authority(self, scopes: list[str]) -> None:
        """Set the delegated scope set (per-system). An empty list means the deputy grants nothing —
        every authorization escalates to the owner."""
        with self._conn() as c:
            c.execute(
                "INSERT INTO system_setting (key,value,updated_at) "
                "VALUES ('deputy_delegated_authority',?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (",".join(s.strip() for s in scopes if s.strip()), _now()),
            )

    # --- learning master switch (WI-8) -------------------------------------------
    def get_learning_enabled(self) -> bool:
        """Whether capture sweeps (idle / phase / completion) may fire. Default OFF — background
        learning spends tokens on its own, so it's opt-in. Capture is fully automatic; there is no
        chat-side capture surface, so this switch governs ALL capture."""
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting WHERE key='learning_enabled'").fetchone()
            return bool(r and str(r["value"]).strip().lower() in ("1", "true", "on", "yes"))

    def set_learning_enabled(self, enabled: bool) -> None:
        """Flip the automatic-learning master switch."""
        with self._conn() as c:
            c.execute(
                "INSERT INTO system_setting (key,value,updated_at) VALUES ('learning_enabled',?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                ("true" if enabled else "false", _now()),
            )

    def get_repo_learning(self, repo_id: str) -> bool:
        """Whether THIS repo participates in automatic capture. Default True (absent row = on);
        the global master switch still gates everything above this."""
        with self._conn() as c:
            r = c.execute("SELECT enabled FROM repo_learning WHERE repo_id=?", (repo_id,)).fetchone()
            return True if r is None else bool(r["enabled"])

    def set_repo_learning(self, repo_id: str, enabled: bool) -> None:
        """Opt a single repo in/out of automatic capture (independent of the master switch)."""
        with self._conn() as c:
            c.execute(
                "INSERT INTO repo_learning (repo_id,enabled,updated_at) VALUES (?,?,?)"
                " ON CONFLICT(repo_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at",
                (repo_id, 1 if enabled else 0, _now()),
            )

    def learning_enabled_for(self, repo_id: str) -> bool:
        """Effective capture gate for a repo: the master switch AND this repo's participation."""
        return self.get_learning_enabled() and self.get_repo_learning(repo_id)

    # --- autopilot concurrency (per-project launch breaker, slice 3) --------------
    AUTOPILOT_CONCURRENCY_DEFAULT = 4

    def get_autopilot_concurrency(self, repo_id: str) -> int:
        """Max autopilot items allowed in the build⟷vet loop at once for this repo. Absent row →
        the default (4). Floored at 1 — a cap of 0 would wedge every autopilot item forever."""
        with self._conn() as c:
            r = c.execute("SELECT concurrency FROM repo_autopilot WHERE repo_id=?",
                          (repo_id,)).fetchone()
        if r is None or r["concurrency"] is None:
            return self.AUTOPILOT_CONCURRENCY_DEFAULT
        return max(1, int(r["concurrency"]))

    def set_autopilot_concurrency(self, repo_id: str, n: int) -> int:
        """Set the per-repo autopilot concurrency cap (floored at 1). Returns the stored value."""
        n = max(1, int(n))
        with self._conn() as c:
            c.execute(
                "INSERT INTO repo_autopilot (repo_id,concurrency,updated_at) VALUES (?,?,?)"
                " ON CONFLICT(repo_id) DO UPDATE SET concurrency=excluded.concurrency,"
                " updated_at=excluded.updated_at",
                (repo_id, n, _now()),
            )
        return n

    # --- deputy (autopilot gate judge, slice 4) -----------------------------------
    # GLOBAL settings (unlike the per-repo concurrency cap): a deputy behaves the same on every
    # project — only its escalation readiness (strictness) and whether it runs at all are owner-set.
    DEPUTY_STRICTNESS_LEVELS = ("low", "medium", "high", "extra")
    DEPUTY_STRICTNESS_DEFAULT = "medium"
    # Strictness is set PER GATE — a project can want a light touch at triage but a cautious hand at
    # review. These are the three gates a deputy judges (mirrors services.deputy.DEPUTY_GATE_PHASES).
    DEPUTY_GATES = ("triage", "plan", "review")

    def get_deputy_enabled(self) -> bool:
        """Whether a deputy judges autopilot gates. Default ON — 'autopilot without a deputy' is the
        design's named DANGEROUS config (a gate advanced by nobody), so once the deputy exists every
        autopilot gate is judged unless the owner explicitly opts out. OFF reverts to the slice-2
        unsupervised auto-advance."""
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting WHERE key='deputy_enabled'").fetchone()
        if r is None or r["value"] is None:
            return True
        return str(r["value"]).strip().lower() in ("1", "true", "on", "yes")

    def set_deputy_enabled(self, enabled: bool) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO system_setting (key,value,updated_at) VALUES ('deputy_enabled',?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                ("true" if enabled else "false", _now()),
            )

    def deputy_strictness_map(self) -> dict:
        """Per-gate escalation dial: {triage, plan, review} → low·medium·high·extra (design §6).
        Each gate tunes only its own escalation THRESHOLD; the refusal floor holds at every level.
        Stored as one JSON object under `deputy_strictness`. Tolerant of a legacy plain-scalar value
        (pre-per-gate) by broadcasting it to all gates, and fills any missing/invalid gate with the
        default — so the map is always complete and every level is valid."""
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting WHERE key='deputy_strictness'").fetchone()
        raw = (r["value"] if r else None) or ""
        stored: dict = {}
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                stored = parsed
            elif isinstance(parsed, str):          # JSON-encoded scalar
                stored = {g: parsed for g in self.DEPUTY_GATES}
        except (ValueError, TypeError):
            if raw:                                # legacy bare scalar (e.g. "high") — broadcast it
                stored = {g: raw for g in self.DEPUTY_GATES}
        return {g: (stored.get(g) if stored.get(g) in self.DEPUTY_STRICTNESS_LEVELS
                    else self.DEPUTY_STRICTNESS_DEFAULT)
                for g in self.DEPUTY_GATES}

    def get_deputy_strictness(self, gate: str) -> str:
        """One gate's strictness — used at dispatch to build that gate's preamble. Unknown gate →
        the default (medium)."""
        return self.deputy_strictness_map().get(gate, self.DEPUTY_STRICTNESS_DEFAULT)

    def set_deputy_strictness(self, gate: str, level: str) -> dict:
        """Set ONE gate's strictness dial. Rejects an unknown gate or level (never silently stores
        garbage that would fall back at read time and hide the typo). Returns the full updated map."""
        if gate not in self.DEPUTY_GATES:
            raise ValueError(f"unknown deputy gate {gate!r}; "
                             f"expected one of {self.DEPUTY_GATES}")
        if level not in self.DEPUTY_STRICTNESS_LEVELS:
            raise ValueError(f"unknown deputy strictness {level!r}; "
                             f"expected one of {self.DEPUTY_STRICTNESS_LEVELS}")
        m = self.deputy_strictness_map()
        m[gate] = level
        with self._conn() as c:
            c.execute(
                "INSERT INTO system_setting (key,value,updated_at) VALUES ('deputy_strictness',?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (json.dumps(m), _now()),
            )
        return m

    # --- capture-sweep tuning (idle / heartbeat / min-user-message gate) ----------
    # Defaults live in services.learning; these are the runtime overrides the owner sets from
    # Quick config. A value of 0 for min_user_msgs disables the message-count gate.
    _SWEEP_DEFAULTS = {"sweep_idle_seconds": 900, "sweep_poll_seconds": 300, "sweep_min_user_msgs": 1}

    def get_sweep_config(self) -> dict:
        """The three sweep knobs: {idle_seconds, poll_seconds, min_user_msgs}. Missing rows fall
        back to the defaults above."""
        with self._conn() as c:
            rows = dict(
                (r["key"], r["value"])
                for r in c.execute(
                    "SELECT key, value FROM system_setting WHERE key IN (?,?,?)",
                    tuple(self._SWEEP_DEFAULTS),
                ).fetchall()
            )
        def _int(key: str) -> int:
            try:
                return int(rows[key])
            except (KeyError, TypeError, ValueError):
                return self._SWEEP_DEFAULTS[key]
        return {
            "idle_seconds": _int("sweep_idle_seconds"),
            "poll_seconds": _int("sweep_poll_seconds"),
            "min_user_msgs": _int("sweep_min_user_msgs"),
        }

    def set_sweep_config(self, *, idle_seconds: int | None = None,
                         poll_seconds: int | None = None, min_user_msgs: int | None = None) -> dict:
        """Set one or more sweep knobs (None = leave unchanged). Returns the new config."""
        updates = {
            "sweep_idle_seconds": idle_seconds,
            "sweep_poll_seconds": poll_seconds,
            "sweep_min_user_msgs": min_user_msgs,
        }
        with self._conn() as c:
            for key, val in updates.items():
                if val is None:
                    continue
                c.execute(
                    "INSERT INTO system_setting (key,value,updated_at) VALUES (?,?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, str(max(0, int(val))), _now()),
                )
        return self.get_sweep_config()

    # --- compaction runtime tuning (workspace-workflow S8/D11) --------------------
    # `trigger_pct` = context fill (vs the effective window) at which the kernel compacts a
    # work-item session; `by_kind` = per-kind overrides; `min_gain_pct` = the effectiveness
    # threshold (a compaction shrinking the prompt by less than this is a STRIKE). Floor safety
    # (refusing a trigger the incompressible floor alone would exceed) lives in the compaction
    # service — the spine just stores.
    # min_gain default is "auto": the verdict is judged against the session's RECLAIMABLE space
    # (pre − incompressible floor), not a flat %, so preload-heavy sessions aren't false-failed.
    # A manual int (1–95) remains the escape-hatch override.
    _COMPACTION_DEFAULTS = {"compaction_trigger_pct": 80, "compaction_min_gain_pct": "auto"}

    def get_compaction_config(self) -> dict:
        """{trigger_pct, by_kind: {kind: pct}, min_gain_pct} — missing rows fall back to defaults.
        min_gain_pct is "auto" (reclaimable-normalized verdict) or a manual int %."""
        with self._conn() as c:
            rows = {r["key"]: r["value"] for r in c.execute(
                "SELECT key, value FROM system_setting WHERE key IN "
                "('compaction_trigger_pct','compaction_min_gain_pct','compaction_by_kind')"
            ).fetchall()}
        def _int(key: str) -> int:
            try:
                return int(rows[key])
            except (KeyError, TypeError, ValueError):
                return self._COMPACTION_DEFAULTS[key]
        raw_gain = rows.get("compaction_min_gain_pct")
        if raw_gain is None or str(raw_gain).strip().lower() == "auto":
            min_gain = self._COMPACTION_DEFAULTS["compaction_min_gain_pct"]
        else:
            try:
                min_gain = int(raw_gain)
            except (TypeError, ValueError):
                min_gain = self._COMPACTION_DEFAULTS["compaction_min_gain_pct"]
        try:
            by_kind = {str(k): int(v) for k, v in
                       json.loads(rows.get("compaction_by_kind") or "{}").items()}
        except (ValueError, TypeError):
            by_kind = {}
        return {"trigger_pct": _int("compaction_trigger_pct"), "by_kind": by_kind,
                "min_gain_pct": min_gain}

    def set_compaction_config(self, *, trigger_pct: int | None = None,
                              by_kind: dict | None = None,
                              min_gain_pct: int | str | None = None) -> dict:
        """Set one or more compaction knobs (None = leave unchanged). The caller (route) has
        already refused floor-violating values. min_gain_pct: an int % or the string "auto".
        Returns the new config."""
        gain_val = None
        if min_gain_pct is not None:
            gain_val = ("auto" if str(min_gain_pct).strip().lower() == "auto"
                        else str(int(min_gain_pct)))
        updates = {
            "compaction_trigger_pct": None if trigger_pct is None else str(int(trigger_pct)),
            "compaction_min_gain_pct": gain_val,
            "compaction_by_kind": None if by_kind is None else json.dumps(
                {str(k): int(v) for k, v in by_kind.items()}),
        }
        with self._conn() as c:
            for key, val in updates.items():
                if val is None:
                    continue
                c.execute(
                    "INSERT INTO system_setting (key,value,updated_at) VALUES (?,?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, val, _now()),
                )
        return self.get_compaction_config()

    # --- repo visual tag (owner-defined color + icon) ----------------------------
    def get_repo_meta(self, repo_id: str) -> dict:
        """The repo's visual tag: {color, icon} (both may be None = use defaults)."""
        with self._conn() as c:
            r = c.execute("SELECT color, icon FROM repo_meta WHERE repo_id=?", (repo_id,)).fetchone()
            return {"color": r["color"] if r else None, "icon": r["icon"] if r else None}

    def set_repo_meta(self, repo_id: str, *, color: str | None = None, icon: str | None = None) -> dict:
        """Set the repo's visual tag color and/or icon. Pass an empty string to clear a field
        (kept distinct from None = 'leave unchanged'). Returns the new {color, icon}."""
        cur = self.get_repo_meta(repo_id)
        new_color = cur["color"] if color is None else (color or None)
        new_icon = cur["icon"] if icon is None else (icon or None)
        with self._conn() as c:
            if new_color is None and new_icon is None:
                c.execute("DELETE FROM repo_meta WHERE repo_id=?", (repo_id,))
            else:
                c.execute(
                    "INSERT INTO repo_meta (repo_id,color,icon,updated_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(repo_id) DO UPDATE SET color=excluded.color, icon=excluded.icon,"
                    " updated_at=excluded.updated_at",
                    (repo_id, new_color, new_icon, _now()),
                )
        return {"color": new_color, "icon": new_icon}

    # --- composed reads (the System entity, for the dashboard / self-model) ------
    def system(self) -> dict:
        """The System singleton: static config + live half (in-flight runs)."""
        cfg = self.system_config()
        live = self.live_runs()
        return {
            "identity": cfg.identity,
            "version": cfg.version,
            "default_model": self.effective_system_model(),  # runtime override else YAML
            "default_model_static": cfg.default_model,        # the YAML floor (for the config UI)
            "default_model_overridden": self.get_system_model() is not None,
            "default_effort": self.effective_system_effort(),  # runtime override else YAML else "medium"
            "default_effort_overridden": self.get_system_effort() is not None,
            "policy_version": cfg.policy_version,
            "default_repo": cfg.default_repo,
            "learning_enabled": self.get_learning_enabled(),  # auto-sweep master switch (WI-8)
            "deputy_enabled": self.get_deputy_enabled(),       # autopilot gate judge (slice 4)
            "deputy_strictness": self.deputy_strictness_map(),  # {gate: low·medium·high·extra}
            "live_runs": live,
            "running": len(live),
        }


# --------------------------------------------------------------------------- singleton

_SPINE: SystemSpine | None = None


def get_spine() -> SystemSpine:
    """The per-process spine singleton. Each surface process (daemon, Slack) holds its own
    instance over the same `.system.db`; short-lived connections make that multi-process-safe
    (the same model the old file-backed stores used)."""
    global _SPINE
    if _SPINE is None:
        _SPINE = SystemSpine()
    return _SPINE
