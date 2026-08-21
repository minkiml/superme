"""SystemSpine — the authoritative System, Repo, Session and Run data model.

STATIC-meta is git-tracked YAML; LIVE-status is a SQLite DB this module OWNS. FOUR entities: a
Session is resumable, a Run is one execution, and a standalone pass has `session_id=NULL`.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..paths import (
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
# Sessionless passes whose transcript is THROWAWAY. The run ROW is always kept — it is the durable
# telemetry.
DISPOSABLE_FEATURES = {"distill", "sweep", "write"}
# Explicit LEARNING agent jobs, sessionless, counted as "agents" only while running. Work-item
# jobs count separately.
LEARNING_FEATURES = {"distill", "sweep", "capture", "write"}
_RUN_STATUSES = {"running", "done", "aborted", "waiting"}
# Runs that ENDED without a final usage. Every display reports 0: measured usage only, never an
# estimate.
_UNRECONCILED_STATUSES = {"aborted"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(p) -> str:
    """Canonical absolute path string, so equal cwds compare equal."""
    return str(Path(p).resolve())


# Commands that put a file's CONTENTS into context. `ls` and `find` name a path without opening
# it.
_FILE_OPENERS = frozenset({"cat", "bat", "head", "tail", "less", "more", "sed", "awk", "grep",
                           "egrep", "fgrep", "rg", "nl", "strings"})


def _opens_a_file(command: str) -> bool:
    """True if any word in a shell command is a program that reads file contents."""
    return any(w.strip("'\"();|&`$") in _FILE_OPENERS for w in command.split())


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
    # The connect-time onboarding front door, until memory is established. None = let the owner pick.
    onboarding: str | None = None  # "project-init" | "retrofit"
    # `fast` = approving the item merges it; `strict` = the diff gets its own review gate first.
    review_mode: str = REVIEW_MODE_DEFAULT  # not `mode` — `Context.mode` already means core|dev
    # The branch every git site targets: branch-from base, sync source, merge target.
    anchor_branch: str | None = None  # None = derive the repo's own default branch
    # Gitignored paths that are nonetheless SOURCE, copied read-only into a research scratch tree.
    source_ignored: list = field(default_factory=list)  # repo-relative, no globs, never absolute
    # How to boot a server running an ITEM WORKTREE's code, so another instance cannot answer a
    # check.
    vet_env: dict | None = None  # keys: `cmd` · `port_env` · `ready` · `url_env`; None = no vet env

    def __post_init__(self):
        if not self.label:
            self.label = self.id
        self.cwd = Path(self.cwd)
        if self.review_mode not in REVIEW_MODES:
            log.warning("repo %r: unknown review_mode %r; using %r",
                        self.id, self.review_mode, REVIEW_MODE_DEFAULT)
            self.review_mode = REVIEW_MODE_DEFAULT
        self.anchor_branch = (self.anchor_branch or "").strip() or None
        # A block without a `cmd` names nothing to start, so a half-filled entry must not read as
        # a vet env.
        if isinstance(self.vet_env, dict) and not str(self.vet_env.get("cmd") or "").strip():
            log.warning("repo %r: vet_env has no `cmd`; ignoring it", self.id)
            self.vet_env = None
        elif self.vet_env is not None and not isinstance(self.vet_env, dict):
            log.warning("repo %r: vet_env must be a mapping; ignoring it", self.id)
            self.vet_env = None
        # Checked HERE once, and dropped loudly rather than sanitised: a rewritten path is one
        # nobody can find.
        clean: list[str] = []
        for raw in (self.source_ignored or []):
            # Absoluteness is tested on the RAW value: stripping the slash first would make
            # `/etc/passwd` look relative.
            absolute = Path(str(raw).strip()).is_absolute()
            rel = str(raw).strip().strip("/")
            if not rel or absolute or ".." in Path(rel).parts:
                log.warning("repo %r: source_ignored entry %r is not a repo-relative path; "
                            "ignoring it", self.id, raw)
                continue
            clean.append(rel)
        self.source_ignored = clean

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
        """The per-repo operational home for a scope, under the CODE tree. This dir IS
        the per-repo plugin root."""
        return LOCAL_HARNESS_DIR / self.id / scope

    def constitution_home(self, scope: str) -> Path:
        """The per-repo learned-constitution home: one file per item, inside the
        operational cell."""
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
        # Only non-defaults are written, so an untouched entry stays byte-identical.
        if self.review_mode != REVIEW_MODE_DEFAULT:
            d["review_mode"] = self.review_mode
        if self.anchor_branch:
            d["anchor_branch"] = self.anchor_branch
        if self.vet_env:
            d["vet_env"] = dict(self.vet_env)
        if self.source_ignored:
            d["source_ignored"] = list(self.source_ignored)
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
    """Load config/repos.yaml → {id: RepoConfig}. Relative cwds resolve against ROOT_DIR;
    an absent file gives {}."""
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
            source_ignored=spec.get("source_ignored") or [],
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
        """The per-run token amount for DISPLAY: input + cache_creation + output, EXCLUDING
        cache_read.

        Only a PRE-MIGRATION row falls back to the legacy scalar. A run that never returned a final usage
        carries a live estimate, not a measurement."""
        typed = ((row["tok_input"] or 0) + (row["tok_cache_creation"] or 0)
                 + (row["tok_output"] or 0))
        if typed > 0:
            return typed
        try:
            status = row["status"]
        except (IndexError, KeyError):
            status = None
        return 0 if status in _UNRECONCILED_STATUSES else (row["tokens"] or 0)

    def _run_dict(self, r) -> dict:
        """Row → dict for a `run` row, with `tokens` overridden to the 3-type display amount."""
        d = dict(r)
        d["tokens"] = self._display_tokens(r)
        return d

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

    # --- static config (loaded fresh; cheap + always current) -------------------
    def system_config(self) -> SystemConfig:
        return load_system_config(self._system_config_path)

    def repos(self) -> dict[str, RepoConfig]:
        return load_repos(self._repos_config_path)

    def repo(self, repo_id: str) -> RepoConfig | None:
        return self.repos().get(repo_id)

    def add_repo(self, rc: RepoConfig) -> RepoConfig:
        """Register a new repo by APPENDING to config/repos.yaml, so header comments survive.
        `repos()` re-reads every call, so it goes live at once."""
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
        """Edit scalar fields line by line, so untouched lines keep their bytes. None deletes
        a line; validated by re-loading."""
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
        # A field set back to its DEFAULT is stored as absence, so setting and unsetting leaves
        # the file byte-identical.
        if patch.get("review_mode") == REVIEW_MODE_DEFAULT:
            patch["review_mode"] = None
        def _line(key: str, val) -> str:
            # A one-key mapping, not a bare scalar: safe_dump of a scalar emits a whole document.
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
        """Deregister a repo: drop its entry and its runtime kv rows. Forgets the REGISTRATION
        only. Refuses `global`."""
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
        """{repo_id: {label, cwd, disconnected_at}} for every disconnected project.
        Reconnecting does not clear the tombstone — harmless."""
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
        """On startup, flip orphaned `running` runs to `aborted`. Returns one row per orphan so
        the caller can heal the item too."""
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
        """The work-item this session is stamped to, or None. This stamp is the SINGLE source
        of truth for that question."""
        if not session_id:
            return None
        with self._conn() as c:
            r = c.execute("SELECT item_id FROM session WHERE id=?", (session_id,)).fetchone()
            return (r["item_id"] if r else None) or None

    def session_is_onboarding(self, session_id: str | None) -> bool:
        """True if this session ever ran an onboarding turn. The sweep skips them:
        dense with SuperMe reciting its own skills."""
        if not session_id:
            return False
        with self._conn() as c:
            r = c.execute(
                "SELECT 1 FROM run WHERE session_id=? AND feature='onboarding' LIMIT 1",
                (session_id,)).fetchone()
            return r is not None

    def session_is_diagnosis(self, session_id: str | None) -> bool:
        """True for a DIAGNOSIS session. Capture never sweeps it — mining
        meta-observation feeds recursion."""
        if not session_id:
            return False
        with self._conn() as c:
            r = c.execute(
                "SELECT 1 FROM session WHERE id=? AND kind='diagnosis' LIMIT 1", (session_id,),
            ).fetchone()
            return r is not None

    def stamp_session_item(self, session_id: str, item_id: str) -> bool:
        """Stamp a session's durable work-item identity — write-once, so a session can
        never be re-pointed at another item."""
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
        """Stamp a session's durable KIND plus optional subject pointer — write-once, so
        resume trusts the store, not the client."""
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
        """Set or clear a session's owner TITLE override. A blank title reverts to the
        transcript-derived one."""
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
        """The stored (kind, subject_run_id), or None. `kind` is NULL for pre-existing
        sessions — the caller derives it."""
        if not session_id:
            return None
        with self._conn() as c:
            r = c.execute(
                "SELECT kind, subject_run_id, item_id FROM session WHERE id=?", (session_id,),
            ).fetchone()
            return dict(r) if r else None

    def backfill_session_items(self, pairs: list[tuple[str, str]]) -> int:
        """One-time migration: stamp each unstamped (session_id, item_id).
        Write-once, so it never clobbers."""
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
        """Sessions that ran in a workspace. Resumable-only by design — a standalone pass
        never creates a Session."""
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
        """Every session bound to a repo. `repo_id` is the identity that survives a
        worktree; a phase run swaps its cwd."""
        where = ["repo_id=?"]
        if resumable_only:
            where.append("resumable=1")
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM session WHERE {' AND '.join(where)}"
                " ORDER BY datetime(updated_at) DESC", (repo_id,)).fetchall()
            return [dict(r) for r in rows]

    def running_run_count(self, repo_id: str) -> int:
        """Runs executing right now for a repo — the disconnect pre-flight: never rip a
        repo from a live agent."""
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM run WHERE repo_id=? AND status='running'",
                             (repo_id,)).fetchone()[0]

    def session_for_thread(self, thread_ts: str) -> str | None:
        """The session id for a Slack thread (thread_ts is globally unique)."""
        with self._conn() as c:
            r = c.execute("SELECT id FROM session WHERE thread_ts=?", (thread_ts,)).fetchone()
            return r["id"] if r else None

    def delete_session_record(self, session_id: str, *, cause: str = "deleted") -> bool:
        """The one db-layer session removal: hard-delete the resume STATE, LABEL the
        trace left behind.

        Runs, events and artifacts are PRESERVED and stamped `session_fate`. Transcript-FILE deletion
        lives in SessionStore."""
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
        """Open a run row and return its id. `session_id` stays NULL for standalone passes —
        that keeps them out of the picker."""
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO run (repo_id,mode,feature,session_id,item_id,status,model,started_at)"
                " VALUES (?,?,?,?,?,'running',?,?)",
                (repo_id, mode, feature, session_id, item_id, model, _now()),
            )
            return cur.lastrowid

    def start_item_run(self, repo_id: str, *, mode: str = "dev", feature: str = "plan",
                       item_id: str, model: str | None = None, phase: str | None = None) -> int | None:
        """Atomically open a run IFF none is in flight. The guarantee is the UNIQUE index,
        not the SELECT short-circuit."""
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
        """Live-update a running row from one Usage step — an in-flight ESTIMATE. Per-step events
        are cumulative, so summing over-counts."""
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
        """Close a run row, always kept as durable telemetry. `usage` is the whole-turn final SDK
        usage, the authoritative accounting."""
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
        """Write the per-type accounting at finish. `usage` MUST be the whole turn's:
        `Result.usage` covers the parent only, measured 8.3× smaller."""
        if not usage:
            return
        i, cc, cr, o = self._usage_parts(usage)
        c.execute(
            "UPDATE run SET tok_input=?, tok_cache_creation=?, tok_cache_read=?, tok_output=?,"
            " raw_usage=?, tokens=? WHERE id=?",
            (i, cc, cr, o, json.dumps(usage), self._legacy_tokens(i, cc, cr, o), run_id))

    def is_running(self, repo_id: str, mode: str, feature: str | None = None) -> bool:
        """The server-truth run-guard, keyed by (repo × mode [× feature]) and read from the DB,
        not a shadow set."""
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
        """Every in-flight ITEM run with its last sign of life. ONE query,
        because the stall watchdog polls this."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT r.*, COALESCE(MAX(e.created_at), r.started_at) AS quiet_since"
                "  FROM run r LEFT JOIN run_event e ON e.run_id = r.id"
                " WHERE r.status='running' AND r.item_id IS NOT NULL"
                " GROUP BY r.id ORDER BY datetime(r.started_at) ASC"
            ).fetchall()
            return [{**self._run_dict(r), "quiet_since": r["quiet_since"]} for r in rows]

    def runs_for_item(self, repo_id: str, item_id: str) -> list[dict]:
        """Every run this work-item has had, OLDEST-first — the unified timeline. Unbounded
        by design."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM run WHERE repo_id=? AND item_id=? AND discarded_at IS NULL"
                " ORDER BY datetime(started_at) ASC, id ASC", (repo_id, item_id),
            ).fetchall()
            return [self._run_dict(r) for r in rows]

    def subagent_count(self, repo_id: str, item_id: str, *, phase: str) -> int:
        """How many SUBAGENTS this item's runs at `phase` spawned — the unfakeable answer to
        "did it fan out".

        Counts kernel-written `run_event` rows across EVERY run at that phase, discarded runs excluded."""
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(*) AS n FROM run_event e JOIN run r ON r.id = e.run_id"
                " WHERE r.repo_id=? AND r.item_id=? AND r.phase=?"
                "   AND e.kind='subagent' AND e.discarded_at IS NULL AND r.discarded_at IS NULL",
                (repo_id, item_id, phase),
            ).fetchone()
            return int(row["n"] if row else 0)

    def brief_sizes(self, repo_id: str, item_id: str, *, phase: str) -> list[int]:
        """How big each subagent BRIEF was, per spawn at `phase`.

        Size is a PROXY: it proves a brief too short to carry a bar, never that a long one carried the
        right one."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT e.description AS d, LENGTH(e.payload) AS n"
                "  FROM run_event e JOIN run r ON r.id = e.run_id"
                " WHERE r.repo_id=? AND r.item_id=? AND r.phase=?"
                "   AND e.kind='subagent'"
                "   AND (e.payload IS NOT NULL OR e.description LIKE '%brief %')"
                "   AND e.discarded_at IS NULL AND r.discarded_at IS NULL"
                " ORDER BY e.id ASC",
                (repo_id, item_id, phase),
            ).fetchall()
        sizes: list[int] = []
        for row in rows:
            if row["n"] is not None:        # the brief itself — measured, not parsed back out
                sizes.append(int(row["n"]))
                continue
            tail = str(row["d"] or "").rsplit("brief ", 1)[-1]
            digits = "".join(ch for ch in tail if ch.isdigit())
            if digits:
                sizes.append(int(digits))
        return sizes

    def subagent_briefs(self, repo_id: str, item_id: str, *,
                        phase: str) -> list[dict]:
        """Every subagent BRIEF this item's runs sent at `phase`, in spawn order, with its
        text.

        The brief is the whole channel to a spawned worker. Spawns from before briefs were stored are
        absent, not present-and-empty."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT e.run_id, e.created_at, e.description, e.payload"
                "  FROM run_event e JOIN run r ON r.id = e.run_id"
                " WHERE r.repo_id=? AND r.item_id=? AND r.phase=?"
                "   AND e.kind='subagent' AND e.payload IS NOT NULL"
                "   AND e.discarded_at IS NULL AND r.discarded_at IS NULL"
                " ORDER BY e.id ASC",
                (repo_id, item_id, phase),
            ).fetchall()
        return [{"run_id": r["run_id"], "at": r["created_at"],
                 "label": r["description"], "text": r["payload"]} for r in rows]

    def read_hits(self, repo_id: str, item_id: str, *, phase: str, needle: str) -> int:
        """How many times this item's runs at `phase` READ a path containing `needle` — the
        receipt for a directed read.

        Counts the ACT, not the tool: `cat <guide>` is a read. Commands that only NAME a path are not."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT e.name, e.description FROM run_event e JOIN run r ON r.id = e.run_id"
                " WHERE r.repo_id=? AND r.item_id=? AND r.phase=?"
                "   AND e.kind='tool' AND e.name IN ('Read','Bash') AND e.description LIKE ?"
                "   AND e.discarded_at IS NULL AND r.discarded_at IS NULL",
                (repo_id, item_id, phase, f"%{needle}%"),
            ).fetchall()
        return sum(1 for r in rows if r["name"] == "Read"
                   or _opens_a_file(r["description"] or ""))

    def last_phase_run_end(self, repo_id: str, item_id: str, *, phase: str) -> str | None:
        """When this item's most recent FINISHED run at `phase` ended, or None.

        The cutoff for "what changed since this thread last ran" — the phase's own last run, not the
        item's. `ended_at IS NOT NULL` excludes the asking run."""
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

    # Work-item runs are keyed by (repo_id, item_id), one running row per item — the per-item run-
    # lock.
    def bump_item_run(self, repo_id: str, item_id: str, *, add_tokens: int = 0,
                      ctx_pct: int | None = None) -> None:
        """Live in-flight estimate for the item's running row. The authoritative accounting
        is set at finish."""
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
        """Set the live token estimate ABSOLUTELY. Callers dedupe the Usage stream by
        message_id, so each API call counts once."""
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
        """Close the item's running row, keeping the accumulated live token sum. `ctx_pct` is
        the AUTHORITATIVE end-of-turn fill and overwrites the last estimate.

        `session_id` joins item runs to the `session_fate` labeling path. Returns the run id, or None."""
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
        """The most recent context fill recorded for a session — what the compaction trigger
        checks at run START."""
        if not session_id:
            return None
        with self._conn() as c:
            row = c.execute(
                "SELECT ctx_pct FROM run WHERE session_id=? AND ctx_pct IS NOT NULL"
                " AND status!='running' ORDER BY id DESC LIMIT 1", (session_id,),
            ).fetchone()
        return int(row["ctx_pct"]) if row else None

    def session_compacted_pending(self, session_id: str | None) -> str | None:
        """When this session was compacted, if no real turn has run since.
        Self-clearing and restart-proof: read from the run table."""
        if not session_id:
            return None
        with self._conn() as c:
            row = c.execute(
                "SELECT feature, ended_at FROM run WHERE session_id=? AND status!='running'"
                " ORDER BY id DESC LIMIT 1", (session_id,),
            ).fetchone()
        return (row["ended_at"] if row and row["feature"] == "compact" else None)

    def run_tokens(self, run_id: int | None) -> int:
        """The reconciled `tokens` scalar — authoritative once the run finished. A pre-finish
        snapshot OVER-counts."""
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
        """The id of the item's in-flight run. A live frame published with `run_id: null`
        cannot be matched against history."""
        run = self.live_run(repo_id, item_id)
        return int(run["id"]) if run and run.get("id") is not None else None

    def discard_item_trace(self, repo_id: str, item_id: str, *, at: str) -> dict:
        """SOFT-delete every run and run-event row this item has — the re-run's "start
        clean". NOTHING IS DELETED; rows are stamped `discarded_at`.

        Call BEFORE writing `item.rerun`, so that event survives unstamped."""
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
        """Close out an item's in-flight run rows, KEEPING every row.

        THE ROW HALF ONLY. This says the run is over; it does not make it be over. Anything DISPOSING of
        an item wants `daemon.services.runs.stop_item_work`, which cancels the task first."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE run SET status='aborted', ended_at=? WHERE repo_id=? AND item_id=? AND status='running'",
                (_now(), repo_id, item_id))
            return cur.rowcount

    # `log_artifact` is RETIRED: a poorer copy of what `log_run_event` records. Its rows STAY as
    # frozen history.

    # --- run events (the per-RUN observability trail: prompt · reply · tool/skill/agent calls) ---
    def log_run_event(self, *, repo_id: str, kind: str, name: str, description: str | None = None,
                      run_id: int | None = None, item_id: str | None = None,
                      tool_id: str | None = None, parent_tool_id: str | None = None,
                      payload: str | None = None) -> None:
        """Append one event to a run's trail. Pass `run_id`, or `item_id` to resolve the
        running run. Best-effort — never raises."""
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
                    " (run_id,repo_id,item_id,seq,kind,name,description,tool_id,parent_tool_id,"
                    "  payload,created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, repo_id, item_id, seq, kind, name, description, tool_id,
                     parent_tool_id, payload, _now()),
                )
        except Exception:  # noqa: BLE001 — telemetry must never break a turn
            pass

    def record_run_input(self, run_id: int, *, repo_id: str, item_id: str | None, phase: str | None,
                         feature: str | None, background: bool, system_prompt: str,
                         prompt_body: str, system_fragments: str | None = None,
                         turn_surface: str | None = None,
                         authored_extras: str | None = None) -> None:
        """Persist the ACTUAL full input a run sent, one row per run. `system_fragments`,
        `turn_surface` and `authored_extras` are optional JSON. Never breaks a turn."""
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
        """Stamp a run's `feature` tag, so its kept trace and spend bucket correctly.
        Best-effort telemetry."""
        try:
            with self._conn() as c:
                c.execute("UPDATE run SET feature=? WHERE id=?", (feature, int(run_id)))
        except Exception:  # noqa: BLE001 — telemetry must never break a turn
            pass

    def list_run_inputs_for_item(self, item_id: str) -> list[dict]:
        """Every captured-input row for an item, oldest first. Survives the
        throwaway item's folder deletion."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT ri.run_id, ri.phase, ri.feature, ri.created_at, r.started_at, r.status"
                " FROM run_input ri LEFT JOIN run r ON r.id = ri.run_id"
                " WHERE ri.item_id=? ORDER BY ri.run_id ASC", (str(item_id),)).fetchall()
            return [dict(r) for r in rows]

    def get_prompt_extraction_state(self, repo_id: str) -> dict | None:
        """The repo's latest Prompt X-ray probe state, or None. Durable, so the
        tab lists A-links after teardown."""
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
        """Every trail row an item's runs recorded, oldest-first within each run, newest run
        first."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT id, run_id, seq, kind, name, description, tool_id, parent_tool_id, created_at"
                " FROM run_event WHERE repo_id=? AND item_id=? AND discarded_at IS NULL"
                " ORDER BY run_id IS NULL, run_id DESC, seq ASC", (repo_id, item_id),
            ).fetchall()
            return [dict(r) for r in rows]

    def run_stats(self, repo_id: str, *, mode: str | None = None) -> dict[str, dict]:
        """Per-item telemetry over FINISHED runs. Discarded runs are excluded; repo and system
        totals still count them."""
        where = ["repo_id=?", "status!='running'", "item_id IS NOT NULL", "discarded_at IS NULL"]
        args: list = [repo_id]
        if mode is not None:
            where.append("mode=?")
            args.append(mode)
        out: dict[str, dict] = {}
        with self._conn() as c:
            rows = c.execute(
                f"SELECT item_id, status, tokens, tok_input, tok_cache_creation, tok_cache_read,"
                f" tok_output, model, ctx_pct, phase, started_at, ended_at FROM run"
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
            # Per-phase accumulation, BOTH bases: `by_phase` is 3-type, `by_phase_cr` is
            # cache_read. Pre-column runs bucket under "unknown".
            ph = r["phase"] or "unknown"
            s["by_phase"][ph] = s["by_phase"].get(ph, 0) + toks
            s["by_phase_cr"][ph] = s["by_phase_cr"].get(ph, 0) + (r["tok_cache_read"] or 0)
            s["last_tokens"] = toks
            s["last_duration_ms"] = _duration_ms(r["started_at"], r["ended_at"])
            s["last_model"] = r["model"]
            s["last_ctx_pct"] = r["ctx_pct"]
            # The END, not the start: an item whose long run just finished has moved, not been
            # idle.
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
        """System-wide token aggregation. Every token is attributable along TWO axes that
        reconcile by construction: `by_category` and `by_type`.

        A row with four zero columns contributes NOTHING — it never returned a final usage."""
        from .token_taxonomy import (
            category_for, display_feature, CATEGORY_ORDER, CATEGORY_LABELS, COLLAPSED_CATEGORIES,
        )
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
            # `by_type` keeps all four components; the RETURNED accounted amount is 3-type, which
            # is what every by_* bucket sums.
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
            # Retired feature names report under the live name that absorbed them; the DB row
            # keeps its own spelling.
            feat = display_feature(r["feature"])
            by_scope[r["mode"]] = by_scope.get(r["mode"], 0) + amt
            by_feature[feat] = by_feature.get(feat, 0) + amt
            by_feature_cr[feat] = by_feature_cr.get(feat, 0) + r["tcr"]
            cat = category_for(feat)
            cnode = by_category.setdefault(cat, {"total": 0, "features": {}})
            cnode["total"] += amt
            cnode["features"][feat] = cnode["features"].get(feat, 0) + amt
            pr = by_repo.setdefault(r["repo_id"], {
                "total": 0, "runs": 0, "by_scope": {}, "by_feature": {},
                "by_type": _blank_type(), "by_category": {},
            })
            _add_type(pr["by_type"], r)
            pr["total"] += amt
            pr["runs"] += r["n"] or 0
            pr["by_scope"][r["mode"]] = pr["by_scope"].get(r["mode"], 0) + amt
            pr["by_feature"][feat] = pr["by_feature"].get(feat, 0) + amt
            pcat = pr["by_category"].setdefault(cat, {"total": 0, "features": {}})
            pcat["total"] += amt
            pcat["features"][feat] = pcat["features"].get(feat, 0) + amt

        # Each node carries its display name and whether to draw ONE bar — taxonomy decisions
        # travel WITH the tree.
        def _order(tree: dict) -> dict:
            out = {}
            for k in CATEGORY_ORDER:
                if k not in tree:
                    continue
                out[k] = {**tree[k], "label": CATEGORY_LABELS.get(k, k),
                          "collapsed": k in COLLAPSED_CATEGORIES}
            return out

        by_category = _order(by_category)
        for pr in by_repo.values():
            pr["by_category"] = _order(pr["by_category"])

        # "Old projects" — spend whose repo left. Its runs stay in `total`, so without this bucket
        # they would be counted-but-unnameable.
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
        """Per-day token usage for the trend graph, bucketed by LOCAL day. Derived on the
        fly — no materialized rollup."""
        modifier = f"{int(tz_offset):+d} minutes"
        with self._conn() as c:
            rows = c.execute(
                "SELECT date(started_at, ?) AS day, COUNT(*) AS n,"
                " COALESCE(SUM(tok_input),0) AS ti, COALESCE(SUM(tok_cache_creation),0) AS tcc,"
                " COALESCE(SUM(tok_cache_read),0) AS tcr, COALESCE(SUM(tok_output),0) AS to_"
                " FROM run WHERE started_at IS NOT NULL GROUP BY day ORDER BY day",
                (modifier,),
            ).fetchall()
        # A row with an unparseable `started_at` would vanish from the axis without a word. Say so
        # instead.
        by_day = {r["day"]: r for r in rows if r["day"]}
        if (lost := sum(r["n"] for r in rows if not r["day"])):
            log.warning("token_timeseries: %d run(s) have an unparseable started_at and are absent "
                        "from the day axis", lost)
        days: list[dict] = []
        cumulative = 0
        if by_day:
            # A CONTIGUOUS day axis: gaps become zero-days, so bars cannot mis-space and the date
            # axis cannot lie.
            start, end = date.fromisoformat(min(by_day)), date.fromisoformat(max(by_day))
            d = start
            while d <= end:
                key = d.isoformat()
                r = by_day.get(key)
                ti, tcc, tcr, to_, n = (
                    (r["ti"], r["tcc"], r["tcr"], r["to_"], r["n"] or 0) if r else (0, 0, 0, 0, 0)
                )
                # `total` is 3-type; cache_read rides its own field. Four zero columns contribute
                # NOTHING — unmeasured is not an estimate.
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
        """A repo × scope's live status, computed from runs: active, last_activity, current_item."""
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
        """How many CHANNELS this repo has, so the tile and the list cannot disagree.

        A work-item runs a thread per phase but is ONE channel, so its threads count once.
        `include_agent_threads` adds the headless build/vet threads."""
        where = ["repo_id=?"]
        args: list = [repo_id]
        if mode is not None:
            where.append("mode=?")
            args.append(mode)
        if not include_agent_threads and AGENT_THREAD_KINDS:
            where.append(f"(kind IS NULL OR kind NOT IN ({','.join('?' * len(AGENT_THREAD_KINDS))}))")
            args.extend(AGENT_THREAD_KINDS)
        with self._conn() as c:
            return c.execute(
                f"SELECT COUNT(DISTINCT COALESCE(item_id, id)) FROM session"
                f" WHERE {' AND '.join(where)}", args).fetchone()[0]

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
        """The RUN-based half of the agent metric. Idle in-progress items have no live run
        and are counted from the store."""
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

    # `default` is the project's tier; every other role runs on its own, whatever the project runs on.
    ROLES: tuple[str, ...] = ("default", "vet")

    def get_model_override(self, repo_id: str, role: str = "default") -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT model FROM model_override WHERE repo_id=? AND role=?",
                          (repo_id, role)).fetchone()
            return r["model"] if r else None

    def set_model_override(self, repo_id: str, model: str | None, role: str = "default") -> None:
        """Set (or clear, with model=None) one repo-role's runtime model preference."""
        with self._conn() as c:
            if model is None:
                c.execute("DELETE FROM model_override WHERE repo_id=? AND role=?", (repo_id, role))
            else:
                c.execute(
                    "INSERT INTO model_override (repo_id,role,model,updated_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(repo_id,role) DO UPDATE SET model=excluded.model, updated_at=excluded.updated_at",
                    (repo_id, role, model, _now()),
                )

    def all_model_overrides(self) -> dict[str, str]:
        """Every repo's DEFAULT tier — the roster view. Roles are per-repo detail, read by name."""
        with self._conn() as c:
            return {r["repo_id"]: r["model"] for r in
                    c.execute("SELECT repo_id, model FROM model_override WHERE role='default'").fetchall()}

    # --- the model FLOOR below per-repo overrides --------------------------------

    # No owner-settable system default: a floor under a per-repo choice that is always made only
    # gets shadowed.
    def effective_system_model(self) -> str:
        """The default model a repo with no override runs — always a concrete, known
        id, never the opaque CLI default."""
        from .models import DEFAULT_MODEL, normalize_model
        return normalize_model(self.system_config().default_model) or DEFAULT_MODEL

    def effective_model(self, repo_id: str, *, per_call: str | None = None,
                        item_model: str | None = None) -> str:
        """THE model-precedence resolver: per_call → item_model → this repo's default →
        the system default. `per_call` never writes the repo default."""
        return (per_call or item_model or self.get_model_override(repo_id)
                or self.effective_system_model())

    def role_model(self, repo_id: str, role: str, *, item_model: str | None = None) -> str:
        """The model a named ROLE runs on: the item's pick → this repo's tier for that role →
        the floor.

        The project's default is deliberately absent: a judge inheriting the worker's tier is not an
        independent check."""
        return item_model or self.get_model_override(repo_id, role) or self.effective_system_model()

    # --- per-agent model (the autonomous background sub-agents; owner-tunable) ------------------

    # SOURCE OF TRUTH = each sub-agent's own `.md` frontmatter. The code preset is the fallback.
    @staticmethod
    def _agent_md_path(feature: str):
        from .models import AGENT_MD_NAME
        from ..paths import DEV_PLUGIN_DIR
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
        """The concrete, latest model a background sub-agent runs on: its `.md` tier alias
        resolved here, else the code preset."""
        from .models import agent_model, track_to_latest
        return track_to_latest(self._agent_file_model(feature)) or agent_model(feature)

    def set_agent_model(self, feature: str, model: str | None) -> None:
        """Write a sub-agent's model into its `.md` frontmatter as a TIER ALIAS, so a
        MODEL_TIERS bump needs no file rewrite."""
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
        """The reasoning effort a background sub-agent runs at: its `.md` `effort:`
        field, else 'medium'."""
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
        """Normalize picker overrides to their TIER ALIAS, so old concrete picks
        auto-track a MODEL_TIERS bump. Idempotent."""
        from .models import model_family
        with self._conn() as c:
            row = c.execute("SELECT value FROM system_setting WHERE key='default_model'").fetchone()
            if row and row["value"]:
                alias = model_family(row["value"])
                if alias and alias != row["value"]:
                    c.execute("UPDATE system_setting SET value=?, updated_at=? WHERE key='default_model'",
                              (alias, _now()))
            for r in c.execute("SELECT repo_id, role, model FROM model_override").fetchall():
                alias = model_family(r["model"])
                if alias and alias != r["model"]:
                    c.execute("UPDATE model_override SET model=?, updated_at=? WHERE repo_id=? AND role=?",
                              (alias, _now(), r["repo_id"], r["role"]))

    def reconcile_agent_models(self) -> None:
        """Normalize every sub-agent `.md` model to its TIER ALIAS. Idempotent; run at
        daemon startup."""
        from .models import AGENT_MODEL_FEATURES, model_family
        from .operational import set_frontmatter_field
        for feat in AGENT_MODEL_FEATURES:
            cur = self._agent_file_model(feat)
            alias = model_family(cur)
            path = self._agent_md_path(feat)
            if alias and cur and alias != cur and path and path.is_file():
                set_frontmatter_field(path, "model", alias)

    def agent_model_config(self) -> list[dict]:
        """The tunable background sub-agents in display order: label, scope, tracked tier,
        and the concrete it resolves to."""
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

    def get_effort_override(self, repo_id: str, role: str = "default") -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT effort FROM effort_override WHERE repo_id=? AND role=?",
                          (repo_id, role)).fetchone()
            return r["effort"] if r else None

    def set_effort_override(self, repo_id: str, effort: str | None, role: str = "default") -> None:
        """Set (or clear, with effort=None) one repo-role's runtime reasoning-effort preference."""
        with self._conn() as c:
            if effort is None:
                c.execute("DELETE FROM effort_override WHERE repo_id=? AND role=?", (repo_id, role))
            else:
                c.execute(
                    "INSERT INTO effort_override (repo_id,role,effort,updated_at) VALUES (?,?,?,?)"
                    " ON CONFLICT(repo_id,role) DO UPDATE SET effort=excluded.effort, updated_at=excluded.updated_at",
                    (repo_id, role, effort, _now()),
                )

    def all_effort_overrides(self) -> dict[str, str]:
        """Every repo's DEFAULT effort — see all_model_overrides."""
        with self._conn() as c:
            return {r["repo_id"]: r["effort"] for r in
                    c.execute("SELECT repo_id, effort FROM effort_override WHERE role='default'").fetchall()}

    def effective_system_effort(self) -> str:
        """The default effort a repo with no override runs: the YAML default, else
        the built-in floor."""
        return self.system_config().default_effort or self.DEFAULT_EFFORT

    def effective_effort(self, repo_id: str, *, per_call: str | None = None,
                         item_effort: str | None = None) -> str:
        """The effort a turn should use, mirroring `effective_model`'s precedence:
        per_call → item → repo → system. Never None."""
        return (per_call or item_effort or self.get_effort_override(repo_id)
                or self.effective_system_effort())

    def role_effort(self, repo_id: str, role: str, *, item_effort: str | None = None) -> str:
        """The effort a named ROLE runs at — mirrors role_model, project default excluded."""
        return item_effort or self.get_effort_override(repo_id, role) or self.effective_system_effort()

    # --- build⟷vet loop (build-vet-loop §5) ---------------------------------------

    # Token budget is the PRIMARY breaker: measured spend, not a cycle count. An item's own
    # `loop_budget` wins over this default.
    DEFAULT_LOOP_BUDGET = 500_000

    def item_phase_tokens(self, repo_id: str, item_id: str,
                          phases: tuple[str, ...] = ("build", "vet")) -> int:
        """An item's 3-type spend over the given phases, live and finished.

        Rows with no typed usage FALL BACK to `tokens`, unlike `_display_tokens`: an aborted run's tokens
        were really spent. Discarded runs are excluded, so the breaker inherits no spent budget."""
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

    # `loop_autorun` is RETIRED: it rested items at `awaiting_human` INSIDE a human-free stretch.
    # The token budget is the loop's ceiling.

    # --- delegated deputy authority (BV-A2.2) ------------------------------------

    # The deputy may authorize changes that SYNC the contract to reality; the owner reserves
    # changes that DEFINE intent.
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
        """Whether capture sweeps may fire. Default OFF — background learning spends
        tokens on its own, so it is opt-in."""
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
        """Whether THIS repo participates in automatic capture. Default True; the global
        master switch still gates it."""
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
        """Max autopilot items in the build⟷vet loop at once. Floored at 1 —
        a cap of 0 would wedge every autopilot item."""
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

    # GLOBAL, unlike the per-repo concurrency cap: a deputy behaves the same on every project.
    DEPUTY_STRICTNESS_LEVELS = ("low", "medium", "high", "extra")
    DEPUTY_STRICTNESS_DEFAULT = "medium"
    # Strictness is PER GATE — a light touch at triage, a cautious hand at review. Mirrors
    # `services.deputy.DEPUTY_GATE_PHASES`.
    DEPUTY_GATES = ("triage", "plan", "review")

    def get_deputy_enabled(self) -> bool:
        """Whether a deputy judges autopilot gates. Default ON: autopilot without a
        deputy is a gate advanced by nobody."""
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

    # The deputy's own tier, SYSTEM-scope: one judge across every project, never inheriting a
    # project's tier.
    def get_deputy_model(self) -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting WHERE key='deputy_model'").fetchone()
            return (r["value"] or None) if r else None

    def set_deputy_model(self, model: str | None) -> None:
        from .models import model_family
        alias = model_family(model) if model else None
        with self._conn() as c:
            if alias is None:
                c.execute("DELETE FROM system_setting WHERE key='deputy_model'")
            else:
                c.execute(
                    "INSERT INTO system_setting (key,value,updated_at) VALUES ('deputy_model',?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (alias, _now()),
                )

    def get_deputy_effort(self) -> str | None:
        with self._conn() as c:
            r = c.execute("SELECT value FROM system_setting WHERE key='deputy_effort'").fetchone()
            return (r["value"] or None) if r else None

    def set_deputy_effort(self, effort: str | None) -> None:
        with self._conn() as c:
            if effort is None:
                c.execute("DELETE FROM system_setting WHERE key='deputy_effort'")
            else:
                c.execute(
                    "INSERT INTO system_setting (key,value,updated_at) VALUES ('deputy_effort',?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (effort, _now()),
                )

    def deputy_params(self, *, item_model: str | None = None,
                      item_effort: str | None = None) -> tuple[str, str]:
        """(model, effort) for a deputy turn: the item's own pick → the system deputy tier →
        the floor. The project's tier is absent."""
        return (item_model or self.get_deputy_model() or self.effective_system_model(),
                item_effort or self.get_deputy_effort() or self.effective_system_effort())

    def deputy_strictness_map(self) -> dict:
        """Per-gate escalation dial: {triage, plan, review} → low·medium·high·extra.
        Always complete — missing or invalid gates fill from the default."""
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
        """Set ONE gate's strictness dial. Rejects an unknown gate or level rather
        than storing garbage that hides the typo."""
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

    # Defaults live in `services.learning`; these are the runtime overrides. `min_user_msgs: 0`
    # disables the gate.
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

    # `min_gain_pct` defaults to "auto": judged against RECLAIMABLE space, not a flat %, so
    # preload-heavy sessions are not false-failed.
    _COMPACTION_DEFAULTS = {"compaction_trigger_pct": 80, "compaction_min_gain_pct": "auto"}

    def get_compaction_config(self) -> dict:
        """{trigger_pct, by_kind, min_gain_pct} — missing rows fall back to defaults.
        `min_gain_pct` is "auto" or a manual int %."""
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
        """Set one or more compaction knobs; None leaves a knob unchanged. The route
        has already refused floor-violating values."""
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
        """Set the repo's visual tag color and/or icon. An empty string clears a field;
        None leaves it unchanged."""
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
            # What a repo with NO override runs. Not settable here — a repo sets its own.
            "default_model": self.effective_system_model(),
            "default_effort": self.effective_system_effort(),
            "policy_version": cfg.policy_version,
            "default_repo": cfg.default_repo,
            "learning_enabled": self.get_learning_enabled(),  # auto-sweep master switch (WI-8)
            "deputy_enabled": self.get_deputy_enabled(),       # autopilot gate judge (slice 4)
            "deputy_strictness": self.deputy_strictness_map(),  # {gate: low·medium·high·extra}
            # "Unset" and "unset, which means Sonnet" are different answers: the picker needs
            # the first, the caption the second.
            "deputy_model": self.get_deputy_model(),
            "deputy_effort": self.get_deputy_effort(),
            "deputy_effective_model": self.deputy_params()[0],
            "deputy_effective_effort": self.deputy_params()[1],
            "live_runs": live,
            "running": len(live),
        }


# --------------------------------------------------------------------------- singleton

_SPINE: SystemSpine | None = None


def get_spine() -> SystemSpine:
    """The per-process spine singleton. Short-lived connections over one `.system.db` make
    it multi-process-safe."""
    global _SPINE
    if _SPINE is None:
        _SPINE = SystemSpine()
    return _SPINE
