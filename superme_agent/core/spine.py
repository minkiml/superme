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

    def __post_init__(self):
        if not self.label:
            self.label = self.id
        self.cwd = Path(self.cwd)

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
                       ended_at TEXT
                   )"""
            )
            # Token accounting (token-usage-tracking-spec): the four Anthropic usage fields kept
            # SEPARATE (not fused into `tokens`) so both breakdowns — semantic (by feature) and
            # systematic (by token type) — reconcile against the SAME rows, and cache_read is NEVER
            # dropped. `raw_usage` preserves the complete SDK usage dict (forward-compat: a new usage
            # field is retained even before it earns a typed column). The legacy `tokens` scalar is
            # KEPT and still populated (= input+output+cache_creation) for back-compat with existing
            # telemetry/guard UIs; pre-migration rows have zero typed columns and surface in a labeled
            # "legacy (unsplit)" bucket. Additive ALTERs below migrate an existing .system.db.
            self._ensure_columns(c, "run", {
                "tok_input": "INTEGER NOT NULL DEFAULT 0",
                "tok_cache_creation": "INTEGER NOT NULL DEFAULT 0",
                "tok_cache_read": "INTEGER NOT NULL DEFAULT 0",
                "tok_output": "INTEGER NOT NULL DEFAULT 0",
                "raw_usage": "TEXT",
                # The work-item phase this run happened IN (plan_design / build_eval / …), stamped at
                # run open so a work-item's tokens can be accumulated per-phase. NULL for chat/headless
                # (non-item) runs and for item runs opened before this column existed.
                "phase": "TEXT",
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
                       created_at TEXT NOT NULL
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_artifact_item ON run_artifact(repo_id, item_id)")
            # RUN_EVENT — the per-RUN observability trail (any run, incl. session-less headless):
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
                       created_at TEXT NOT NULL
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_event_run ON run_event(run_id)")
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

    def repo_for_cwd(self, cwd) -> str | None:
        """Reverse-resolve a cwd to a repo id (the logical key for a session)."""
        target = _norm(cwd)
        for rid, rc in self.repos().items():
            if _norm(rc.cwd) == target:
                return rid
        return None

    # --- startup reconcile ------------------------------------------------------
    def reconcile(self) -> int:
        """On daemon startup, flip orphaned `running` runs → `aborted` (the daemon that owned
        them is gone). Returns the count. Leaves an auditable trace instead of a silent vanish."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE run SET status='aborted', ended_at=? WHERE status='running'",
                (_now(),),
            )
            n = cur.rowcount
        if n:
            log.info("spine reconcile: %d orphaned run(s) → aborted", n)
        return n

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

    def stamp_session_item(self, session_id: str, item_id: str) -> bool:
        """Stamp a session's durable work-item identity — write-once (IMMUTABLE): only sets item_id
        when it is currently NULL, so a work-item session can never be re-pointed to a different item
        (work-item-session-recognition-prd). Idempotent if already stamped to the same item. Returns
        True if this call set it. Called at the two existing session-persistence points (bound-turn
        finish, headless /plan) — never from a user-facing 'create session' path, which is what keeps
        work-item sessions from being minted manually."""
        if not session_id or not item_id:
            return False
        with self._conn() as c:
            cur = c.execute(
                "UPDATE session SET item_id=?, updated_at=? WHERE id=? AND item_id IS NULL",
                (item_id, _now(), session_id),
            )
            return cur.rowcount > 0

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

    def session_for_thread(self, thread_ts: str) -> str | None:
        """The session id for a Slack thread (thread_ts is globally unique)."""
        with self._conn() as c:
            r = c.execute("SELECT id FROM session WHERE thread_ts=?", (thread_ts,)).fetchone()
            return r["id"] if r else None

    def forget_session(self, session_id: str) -> bool:
        """The SINGLE session-cascade (session-agent-lifecycle-prd): remove a session AND everything
        keyed to it — its runs, their run_events + run_artifacts, and its sweep watermarks — so no
        stale dead item is left dangling. KNOWLEDGE is never touched (general docs / work-items /
        memory are repo/item-keyed, not session-keyed — operational ⟂ knowledge), so a session delete
        keeps the value the work produced. Both callers use this; forget keeps the transcript FILE,
        purge also removes it. Idempotent. Returns True if a session row existed."""
        if not session_id:
            return False
        with self._conn() as c:
            run_ids = [r[0] for r in c.execute("SELECT id FROM run WHERE session_id=?", (session_id,))]
            if run_ids:
                ph = ",".join("?" * len(run_ids))
                c.execute(f"DELETE FROM run_event WHERE run_id IN ({ph})", run_ids)
                c.execute(f"DELETE FROM run_artifact WHERE run_id IN ({ph})", run_ids)
                c.execute("DELETE FROM run WHERE session_id=?", (session_id,))
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
        the SDK actually resolved for the run (headless features don't request one up front).

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
        """Write the AUTHORITATIVE per-type accounting at finish (see finish_run docstring).

        One run == one turn, so `usage` (the whole-turn Result usage, aggregated across iterations by
        the SDK) IS the run's total. Set the four typed columns absolutely from it, store the full raw
        blob, and reconcile the legacy `tokens` scalar to match (overriding the in-flight estimate).
        No-op when no final usage arrived (e.g. an errored turn) — the live estimate is kept."""
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

    def finish_item_run(self, repo_id: str, item_id: str, *, run_status: str = "done",
                        fallback_tokens: int | None = None,
                        usage: dict | None = None) -> int | None:
        """Close the item's running row (status done|aborted), keeping the accumulated live token
        sum — or `fallback_tokens` if no Usage steps arrived. `usage` (whole-turn final dict) is the
        typed-column fallback (see finish_run). Item runs are durable (never purged). Returns the run
        id, or None if nothing was running."""
        run_status = run_status if run_status in _RUN_STATUSES else "done"
        with self._conn() as c:
            row = c.execute(
                "SELECT id, tokens FROM run WHERE repo_id=? AND item_id=? AND status='running'"
                " ORDER BY datetime(started_at) DESC LIMIT 1", (repo_id, item_id),
            ).fetchone()
            if row is None:
                return None
            tokens = row["tokens"] or fallback_tokens or 0
            c.execute("UPDATE run SET status=?, ended_at=?, tokens=? WHERE id=?",
                      (run_status, _now(), int(tokens), row["id"]))
            self._finish_usage_apply(c, row["id"], usage)  # authoritative per-type + reconciled tokens
            return row["id"]

    def live_run(self, repo_id: str, item_id: str) -> dict | None:
        """The item's currently-running row (live time/tokens/model/ctx_pct), or None."""
        with self._conn() as c:
            r = c.execute(
                "SELECT * FROM run WHERE repo_id=? AND item_id=? AND status='running'"
                " ORDER BY datetime(started_at) DESC LIMIT 1", (repo_id, item_id),
            ).fetchone()
            return self._run_dict(r) if r else None

    def is_item_running(self, repo_id: str, item_id: str) -> bool:
        """The per-item run-lock: True iff any run is in flight for this work-item."""
        return self.live_run(repo_id, item_id) is not None

    def delete_item_runs(self, repo_id: str, item_id: str) -> int:
        """Drop all run rows for a work-item (called when the item is hard-deleted)."""
        with self._conn() as c:
            c.execute("DELETE FROM run_artifact WHERE repo_id=? AND item_id=?", (repo_id, item_id))
            c.execute("DELETE FROM run_event WHERE repo_id=? AND item_id=?", (repo_id, item_id))
            cur = c.execute("DELETE FROM run WHERE repo_id=? AND item_id=?", (repo_id, item_id))
            return cur.rowcount

    # --- run artifacts (the tool / sub-agent / skill call-trail per work-item) ---
    def log_artifact(self, repo_id: str, item_id: str, *, kind: str, name: str,
                     description: str | None = None) -> None:
        """Record one invocation the item's currently-running agent made. Tied to the live run
        (so calls group by run); `seq` orders them within that run. Best-effort — never raises
        into the turn loop."""
        with self._conn() as c:
            run = c.execute(
                "SELECT id FROM run WHERE repo_id=? AND item_id=? AND status='running'"
                " ORDER BY datetime(started_at) DESC LIMIT 1", (repo_id, item_id),
            ).fetchone()
            run_id = run["id"] if run else None
            seq = c.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS n FROM run_artifact"
                " WHERE repo_id=? AND item_id=? AND run_id IS ?", (repo_id, item_id, run_id),
            ).fetchone()["n"]
            c.execute(
                "INSERT INTO run_artifact (run_id,repo_id,item_id,seq,kind,name,description,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (run_id, repo_id, item_id, seq, kind, name, description, _now()),
            )

    # --- run events (the per-RUN observability trail: prompt · reply · tool/skill/agent calls) ---
    def log_run_event(self, *, repo_id: str, kind: str, name: str, description: str | None = None,
                      run_id: int | None = None, item_id: str | None = None) -> None:
        """Append one event to a run's trail (`seq` orders within the run). Pass `run_id` directly
        (chat / headless), or `item_id` to resolve the item's currently-running run. Best-effort —
        never raises into the turn loop."""
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
                    "INSERT INTO run_event (run_id,repo_id,item_id,seq,kind,name,description,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (run_id, repo_id, item_id, seq, kind, name, description, _now()),
                )
        except Exception:  # noqa: BLE001 — telemetry must never break a turn
            pass

    def events_for_run(self, run_id: int) -> list[dict]:
        """The full per-run trail (prompt · replies · calls), in order — powers the Activity trace."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, seq, kind, name, description, created_at FROM run_event"
                " WHERE run_id=? ORDER BY seq ASC", (int(run_id),),
            ).fetchall()
            return [dict(r) for r in rows]

    def artifacts_for_item(self, repo_id: str, item_id: str) -> list[dict]:
        """Every artifact a work-item's runs called, oldest-first within each run, newest run
        first — the call-trail for the work-item detail's Artifacts tab."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, run_id, seq, kind, name, description, created_at FROM run_artifact"
                " WHERE repo_id=? AND item_id=?"
                " ORDER BY run_id IS NULL, run_id DESC, seq ASC", (repo_id, item_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def run_stats(self, repo_id: str, *, mode: str | None = None) -> dict[str, dict]:
        """Per-item accumulated telemetry over FINISHED item runs (the card totals): {item_id:
        {total_tokens, runs, last_tokens, last_duration_ms, last_model, last_context_pct}}.
        Running rows are excluded (their live figures come from live_run)."""
        where = ["repo_id=?", "status!='running'", "item_id IS NOT NULL"]
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
                                              "last_context_pct": None, "by_phase": {}, "by_phase_cr": {}})
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
            s["last_context_pct"] = r["ctx_pct"]
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
          • Breakdown 2 — SYSTEMATIC (`by_type`): input / cache_creation / cache_read / output,
            plus a `legacy` bucket for pre-migration rows that only have the old collapsed scalar.

        Per row, the accounted amount = input + cache_creation + output (3-type, EXCLUDES cache_read)
        if the typed columns are present, else the legacy `tokens` scalar (pre-split rows, already
        3-type). `total` and every by_* bucket sum this 3-type amount — cache_read is kept in `by_type`
        (never lost) so the full 4-type volume = `total + by_type["cache_read"]` (the dashboard toggle).
        Sums EVERY run row, so in-flight spend is included. SuperMe-context only. Back-compat:
        `total`/`by_scope`/`by_feature` are retained. (v1 caveat: forge-eval spend isn't in the spine.)"""
        from .token_taxonomy import category_for, CATEGORY_ORDER
        typed = "tok_input+tok_cache_creation+tok_cache_read+tok_output"
        with self._conn() as c:
            rows = c.execute(
                f"SELECT repo_id, mode, feature, COUNT(*) AS n,"
                f" COALESCE(SUM(tok_input),0) AS ti, COALESCE(SUM(tok_cache_creation),0) AS tcc,"
                f" COALESCE(SUM(tok_cache_read),0) AS tcr, COALESCE(SUM(tok_output),0) AS to_,"
                f" COALESCE(SUM(CASE WHEN ({typed})=0 THEN tokens ELSE 0 END),0) AS legacy"
                " FROM run GROUP BY repo_id, mode, feature"
            ).fetchall()

        def _blank_type() -> dict:
            return {"input": 0, "cache_creation": 0, "cache_read": 0, "output": 0, "legacy": 0}

        def _add_type(d: dict, r) -> int:
            # by_type accumulates ALL four components (cache_read included, so it stays inspectable);
            # the RETURNED accounted amount is 3-type (EXCLUDES cache_read) — that's what `total` and
            # every by_* bucket sum, so the dashboard default + activity + work-item all read 3-type.
            # The full 4-type volume = total + by_type["cache_read"] (the dashboard toggle adds it).
            d["input"] += r["ti"]; d["cache_creation"] += r["tcc"]
            d["cache_read"] += r["tcr"]; d["output"] += r["to_"]; d["legacy"] += r["legacy"]
            return r["ti"] + r["tcc"] + r["to_"] + r["legacy"]

        total = 0
        by_scope: dict[str, int] = {}
        by_feature: dict[str, int] = {}
        by_feature_cr: dict[str, int] = {}  # per-feature cache_read, so "By operation" can render 4-type
        by_type = _blank_type()
        by_category: dict[str, dict] = {}
        by_repo: dict[str, dict] = {}
        for r in rows:
            amt = _add_type(by_type, r)  # the row-group's accounted amount (typed or legacy)
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
        }

    def token_timeseries(self, tz_offset: int = 0) -> dict:
        """Per-day token usage for the trend graph (token-usage-tracking-spec, decision 7).

        Buckets by LOCAL day: `started_at` is stored UTC, and `tz_offset` (minutes to ADD to UTC to
        reach the owner's local time, e.g. +540 for JST — the FE sends `-getTimezoneOffset()`) shifts
        it so "which day" matches the owner's day, not UTC. Each day carries the four token types (+
        the legacy bucket) and a `total`; `cumulative` is the running total of daily totals, so the
        same series renders as per-day bars, a cumulative line, or a stacked breakdown. Derived on the
        fly from the durable run rows — no materialized rollup."""
        modifier = f"{int(tz_offset):+d} minutes"
        typed = "tok_input+tok_cache_creation+tok_cache_read+tok_output"
        with self._conn() as c:
            rows = c.execute(
                "SELECT date(started_at, ?) AS day, COUNT(*) AS n,"
                " COALESCE(SUM(tok_input),0) AS ti, COALESCE(SUM(tok_cache_creation),0) AS tcc,"
                " COALESCE(SUM(tok_cache_read),0) AS tcr, COALESCE(SUM(tok_output),0) AS to_,"
                f" COALESCE(SUM(CASE WHEN ({typed})=0 THEN tokens ELSE 0 END),0) AS legacy"
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
                ti, tcc, tcr, to_, legacy, n = (
                    (r["ti"], r["tcc"], r["tcr"], r["to_"], r["legacy"], r["n"] or 0)
                    if r else (0, 0, 0, 0, 0, 0)
                )
                # `total` is 3-type (EXCLUDES cache_read) to match the dashboard default; cache_read is
                # still carried as its own field so the stacked/toggle view can show the full volume.
                total = ti + tcc + to_ + legacy
                cumulative += total
                days.append({
                    "day": key,
                    "input": ti, "cache_creation": tcc, "cache_read": tcr,
                    "output": to_, "legacy": legacy,
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
    def session_count(self, repo_id: str, mode: str | None = None) -> int:
        where = ["repo_id=?"]
        args: list = [repo_id]
        if mode is not None:
            where.append("mode=?")
            args.append(mode)
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

    def effective_effort(self, repo_id: str) -> str:
        """The effort a turn for `repo_id` should use: per-repo override → system default → 'medium'."""
        return self.get_effort_override(repo_id) or self.effective_system_effort()

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
