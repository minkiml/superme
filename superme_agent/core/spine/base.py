"""The SQLite connection and the schema, which every other mixin reads and writes."""

import sqlite3
from pathlib import Path

from ...paths import REPOS_CONFIG_FILE, REPOS_SEED_FILE, SYSTEM_CONFIG_FILE, SYSTEM_DB_FILE
from .common import log


class Base:
    def __init__(self, db_path: Path = SYSTEM_DB_FILE,
                 system_config: Path = SYSTEM_CONFIG_FILE,
                 repos_config: Path = REPOS_CONFIG_FILE):
        self.db_path = Path(db_path)
        self._system_config_path = Path(system_config)
        self._repos_config_path = Path(repos_config)
        self._seed_repos_config()
        self._init_db()

    def _seed_repos_config(self) -> None:
        """Copy the tracked seed into place on a fresh install; never overwrite a live registry."""
        path = self._repos_config_path
        if path.exists() or not REPOS_SEED_FILE.is_file():
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(REPOS_SEED_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("seeded %s from %s", path.name, REPOS_SEED_FILE.name)
        except OSError as e:
            log.warning("could not seed %s (%s); starting with no repos", path.name, e)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _ensure_columns(c: sqlite3.Connection, table: str, cols: dict[str, str]) -> None:
        """Additive, idempotent migration: ALTER-add any column a pre-existing table lacks.
        Safe on every startup."""
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols.items():
            if name not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    @staticmethod
    def _add_role_key(c: sqlite3.Connection, table: str, value_col: str) -> None:
        """One-time widening of an override table's key to (repo_id, role). SQLite cannot
        ALTER a PRIMARY KEY, so rebuild beside and swap."""
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        if "role" in have or not have:
            return
        c.execute(
            f"""CREATE TABLE {table}_roles (
                    repo_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'default',
                    {value_col} TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (repo_id, role)
                )"""
        )
        c.execute(
            f"INSERT INTO {table}_roles (repo_id, role, {value_col}, updated_at)"
            f" SELECT repo_id, 'default', {value_col}, updated_at FROM {table}"
        )
        c.execute(f"DROP TABLE {table}")
        c.execute(f"ALTER TABLE {table}_roles RENAME TO {table}")

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            # SESSION — the durable, resumable container, keyed by SDK session id. `cwd` is kept
            # because resume needs it.
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
            # The durable, IMMUTABLE identity stamp: non-NULL means a work-item session. Set once,
            # at birth.
            self._ensure_columns(c, "session", {"item_id": "TEXT"})
            # `kind` is the session's AGENT IDENTITY, selecting the preamble; `subject_run_id` is
            # its read-only pointer. Both write-once.
            self._ensure_columns(c, "session", {"kind": "TEXT", "subject_run_id": "INTEGER"})
            # An owner-set TITLE override. NULL means derive from the transcript; when set, it
            # wins in list and read.
            self._ensure_columns(c, "session", {"title": "TEXT"})
            c.execute("CREATE INDEX IF NOT EXISTS idx_session_repo ON session(repo_id, mode)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_session_cwd ON session(cwd)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_session_item ON session(item_id)")
            # RUN — a turn, or a standalone workflow pass. Written at START, so live and
            # historical share one query.
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
            # The four usage fields stay SEPARATE, so both breakdowns reconcile against the same
            # rows. `raw_usage` keeps the whole SDK dict.
            self._ensure_columns(c, "run", {
                "discarded_at": "TEXT",
                "tok_input": "INTEGER NOT NULL DEFAULT 0",
                "tok_cache_creation": "INTEGER NOT NULL DEFAULT 0",
                "tok_cache_read": "INTEGER NOT NULL DEFAULT 0",
                "tok_output": "INTEGER NOT NULL DEFAULT 0",
                "raw_usage": "TEXT",
                # The work-item phase this run happened IN, stamped at open, so tokens accumulate
                # per-phase. NULL for non-item runs.
                "phase": "TEXT",
                # The fate of this run's origin session. The RUN is never deleted; this labels the trace whose
                # session is gone.
                "session_fate": "TEXT",
                # How a BACKGROUND run's completion report ended. NULL for interactive turns,
                # which file none.
                "outcome": "TEXT",
            })
            c.execute("CREATE INDEX IF NOT EXISTS idx_run_guard ON run(repo_id, mode, feature, status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_run_item ON run(repo_id, item_id)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_run_session ON run(session_id)")
            # The per-item run-lock, enforced by the DB. NULL item_ids are distinct in SQLite, so
            # only item-bound runs are constrained.
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_run_one_live"
                      " ON run(repo_id, item_id) WHERE status='running' AND item_id IS NOT NULL")
            # Keyed by (repo, ROLE). A role must NOT inherit the project's tier: a judge on the
            # worker's model checks nothing.
            c.execute(
                """CREATE TABLE IF NOT EXISTS model_override (
                       repo_id TEXT NOT NULL,
                       role TEXT NOT NULL DEFAULT 'default',
                       model TEXT,
                       updated_at TEXT NOT NULL,
                       PRIMARY KEY (repo_id, role)
                   )"""
            )
            # EFFORT_OVERRIDE — per-repo runtime reasoning-effort preference (low|medium|high),
            # mirroring model_override, roles and all.
            c.execute(
                """CREATE TABLE IF NOT EXISTS effort_override (
                       repo_id TEXT NOT NULL,
                       role TEXT NOT NULL DEFAULT 'default',
                       effort TEXT,
                       updated_at TEXT NOT NULL,
                       PRIMARY KEY (repo_id, role)
                   )"""
            )
            # A PRIMARY KEY cannot be widened in place. Rebuild once, stamping every existing row
            # as the `default` role.
            self._add_role_key(c, "model_override", "model")
            self._add_role_key(c, "effort_override", "effort")
            # REPO_LEARNING — per-repo participation in automatic capture, under the global master
            # switch. Absent = participate.
            c.execute(
                """CREATE TABLE IF NOT EXISTS repo_learning (
                       repo_id TEXT PRIMARY KEY,
                       enabled INTEGER NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # Max autopilot items in the loop at once — the launch breaker. The right width
            # depends on the project.
            c.execute(
                """CREATE TABLE IF NOT EXISTS repo_autopilot (
                       repo_id TEXT PRIMARY KEY,
                       concurrency INTEGER NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # REPO_META — the owner's VISUAL tag: display color plus optional emoji. Absent = the
            # hashed palette color.
            c.execute(
                """CREATE TABLE IF NOT EXISTS repo_meta (
                       repo_id TEXT PRIMARY KEY,
                       color TEXT,
                       icon TEXT,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # ARCHIVED_REPO — the tombstone a disconnected project leaves, so its preserved runs
            # stay attributable.
            c.execute(
                """CREATE TABLE IF NOT EXISTS archived_repo (
                       repo_id TEXT PRIMARY KEY,
                       label TEXT NOT NULL,
                       cwd TEXT,
                       disconnected_at TEXT NOT NULL
                   )"""
            )
            # SYSTEM_SETTING — runtime overrides of the System singleton's static config. A row
            # wins; absence uses the YAML.
            c.execute(
                """CREATE TABLE IF NOT EXISTS system_setting (
                       key TEXT PRIMARY KEY,
                       value TEXT,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # RUN_ARTIFACT — the tools, sub-agents and skills a run CALLED, one row each. Kept
            # apart from the curated event log.
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
            # RUN_EVENT — the per-RUN observability trail, in `seq` order. Keyed by `run_id`, so
            # each Activity row has its own thread.
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
            # Stamped alongside `run.discarded_at`: the trace pane reads this table directly, so
            # filtering only `run` would leave rows on screen.
            self._ensure_columns(c, "run_event", {"discarded_at": "TEXT"})
            # `tool_id` pairs a result back to its CALL. `parent_tool_id` names the sub-agent
            # spawn it happened inside.
            self._ensure_columns(c, "run_artifact", {"tool_id": "TEXT", "parent_tool_id": "TEXT"})
            self._ensure_columns(c, "run_event", {"tool_id": "TEXT", "parent_tool_id": "TEXT"})
            # PAYLOAD — the row's full, uncapped text, where the TEXT is what gets audited.
            # `description` is the short trace row.
            self._ensure_columns(c, "run_event", {"payload": "TEXT"})
            # SWEEP_WATERMARK — the capture sweep's per-session position, so a message is NEVER
            # swept twice. Server-truth, not the LLM.
            c.execute(
                """CREATE TABLE IF NOT EXISTS sweep_watermark (
                       session_id TEXT PRIMARY KEY,
                       position INTEGER NOT NULL DEFAULT 0,
                       updated_at TEXT NOT NULL
                   )"""
            )
            # RUN_INPUT — the prompt inspector's capture: the ACTUAL full input a run sent,
            # uncapped, written once at send time.
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
            # `system_fragments` renders per-fragment sub-cards; `turn_surface` records what the
            # turn was ALLOWED to do. Older rows are NULL.
            self._ensure_columns(c, "run_input",
                                 {"system_fragments": "TEXT", "turn_surface": "TEXT",
                                  "authored_extras": "TEXT"})
