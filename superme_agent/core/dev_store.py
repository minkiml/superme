"""DevStore — the dev cockpit's operational SQLite store.

Durable KNOWLEDGE stays in markdown files; operational, queue and run state lives here, keyed by
context_id. Localhost and single-owner, so short-lived connections per call are plenty.
"""

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# An `item` becomes a work-item when pushed; a `note` is the owner's own. Every agent path mints
# `item`.
_KINDS = {"item", "note"}
# open = awaiting push · pushed = promoted. Dropping is a HARD DELETE, so there is no
# "dropped" state.
_STATUSES = {"open", "pushed"}
# A LIST: a row accrues origins over its life. Stored as JSON; legacy scalars are coerced on read.
_ORIGINS = {"user", "agent"}
# Branch-off relations: `blocking`/`parallel` are children that gate the parent; `spawn` is
# provenance-only follow-up.
_SPAWN_RELATIONS = {"blocking", "parallel", "spawn"}

# vet and deputy each resolve on their own chain, never the item's, so each needs its own pair.
_ROLE_COLS = ("vet_model", "vet_effort", "deputy_model", "deputy_effort")


# ONE observer, not 78 instrumented call sites. A plain callback list — core must not know a
# WebSocket exists.
_event_observers: list = []


def subscribe_events(fn) -> None:
    """Register `fn(event)` to run after every `log_event` write. The daemon
    installs exactly one."""
    _event_observers.append(fn)


def _notify_event(row: dict) -> None:
    for fn in list(_event_observers):
        try:
            fn(row)
        except Exception:
            import logging
            logging.getLogger("superme-agent").exception("dev_store event observer failed")


def _norm_origins(value, *, default: str = "user") -> list[str]:
    """Coerce a stored origin (JSON array, legacy scalar, list, None) to a deduped
    list. Unknown → [default]."""
    raw = value
    if isinstance(raw, str):
        s = raw.strip()
        if s.startswith("["):
            try:
                raw = json.loads(s)
            except (ValueError, TypeError):
                raw = [s]
        else:
            raw = [s] if s else []
    if not isinstance(raw, (list, tuple)):
        raw = [raw] if raw else []
    out: list[str] = []
    for v in raw:
        v = str(v).strip()
        if v in _ORIGINS and v not in out:
            out.append(v)
    return out or [default]
# The capture end of capture→distill→ratify→write→publish. A candidate is a behaviour-shaping
# observation the owner reviews later; nothing is applied here.
_MEM_SOURCES = {"agent", "user"}
# The operational form the agent GUESSES a candidate should become (advisory; distill decides).
_MEM_FORM_HINTS = {"constitution", "skill", "agent"}
_MEM_CAND_STATUSES = {"candidate", "processed", "promoted", "rejected", "dropped"}
# `output_form` is WHICH artifact it becomes, `target_scope` WHERE it lands. Both are guesses the
# owner overrides.
_MEM_OUTPUT_FORMS = {"constitution", "skill", "agent"}
_MEM_TARGET_SCOPES = {"repo_dev", "universal_dev", "core"}
# proposed → writing → drafted → published. Terminal: rejected, dropped, superseded.
_MEM_PROP_STATUSES = {"proposed", "writing", "drafted", "published", "rejected", "dropped",
                      "superseded", "retired"}  # retired = a published artifact the owner deleted
_LINE = re.compile(r"^- \[( |x)\]\s*(\d{4}-\d{2}-\d{2})?\s*(\w+)?:?\s*(.*)$")


# The test: a fact about the PROJECT, not a step inside one item's run. Steps belong in the
# drilldown.
REPO_MILESTONE_KINDS = ("git.pr", "git.merge", "git.worktree", "inbox.push", "item.complete")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(s: str) -> str | None:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-") or None


def _parse_spawned_from(value) -> dict | None:
    """Stored JSON `spawned_from` → dict (None on NULL/garbage — callers always see dict|None)."""
    if not value:
        return None
    if isinstance(value, dict):
        return value
    try:
        d = json.loads(value)
        return d if isinstance(d, dict) and d.get("item") else None
    except (ValueError, TypeError):
        return None


class DevStore:
    """Operational state for the dev dashboard. Currently the inbox triage queue."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._init()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """A connection for one call, committed and then CLOSED.

        sqlite's own context manager ends the transaction and leaves the handle open, which
        holds a lock on the file for as long as the process lives."""
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def _init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS inbox (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       context_id TEXT NOT NULL,
                       kind TEXT NOT NULL DEFAULT 'note',
                       text TEXT NOT NULL,
                       tag TEXT,
                       status TEXT NOT NULL DEFAULT 'open',
                       routed_to TEXT,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_inbox_ctx ON inbox(context_id, status)")
            # Run telemetry lives in the spine now; drop the legacy table.
            c.execute("DROP TABLE IF EXISTS runs")
            # The append-only activity firehose. DB-first, so "what happened yesterday?" is a
            # WHERE, not a prose scan.
            c.execute(
                """CREATE TABLE IF NOT EXISTS events (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       context_id TEXT NOT NULL,
                       scope TEXT NOT NULL DEFAULT 'dev',
                       item_id TEXT,
                       kind TEXT NOT NULL,
                       actor TEXT NOT NULL DEFAULT 'daemon',
                       summary TEXT NOT NULL,
                       meta TEXT,
                       created_at TEXT NOT NULL,
                       discarded_at TEXT
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_events_ctx ON events(context_id, created_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_events_item ON events(context_id, item_id)")
            # The re-run soft delete: item readers see the current attempt, the repo feed keeps
            # the whole history.
            ev_cols = {r[1] for r in c.execute("PRAGMA table_info(events)").fetchall()}
            if "discarded_at" not in ev_cols:
                c.execute("ALTER TABLE events ADD COLUMN discarded_at TEXT")
            # `signal` what to do · `rationale` why · `evidence` an instance · `form_hint` the
            # guess.
            c.execute(
                """CREATE TABLE IF NOT EXISTS memory_candidate (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       context_id TEXT NOT NULL,
                       captured_at TEXT NOT NULL,
                       source TEXT NOT NULL DEFAULT 'agent',
                       origin_item_id TEXT,
                       origin_session_id TEXT,
                       scope_hint TEXT NOT NULL DEFAULT 'repo_dev',
                       form_hint TEXT,
                       signal TEXT NOT NULL,
                       rationale TEXT,
                       evidence TEXT,
                       status TEXT NOT NULL DEFAULT 'candidate'
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_memcand_ctx "
                      "ON memory_candidate(context_id, status, captured_at)")
            cand_cols = {r[1] for r in c.execute("PRAGMA table_info(memory_candidate)").fetchall()}
            for col in ("form_hint", "rationale"):  # richer candidate columns; idempotent on old DBs
                if col not in cand_cols:
                    c.execute(f"ALTER TABLE memory_candidate ADD COLUMN {col} TEXT")
            # `form_hint` supersedes the legacy `kind_hint`. Preserve it into form_hint FIRST,
            # then DROP. Guarded, so idempotent.
            if "kind_hint" in cand_cols:
                c.execute("UPDATE memory_candidate SET form_hint=COALESCE(form_hint, kind_hint)")
                c.execute("ALTER TABLE memory_candidate DROP COLUMN kind_hint")
            # The operational-learning PROPOSAL pool. Disk holds only PUBLISHED content, so the
            # rendered artifact stages here.
            c.execute(
                """CREATE TABLE IF NOT EXISTS memory_proposal (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       context_id TEXT NOT NULL,
                       created_at TEXT NOT NULL,
                       output_form TEXT NOT NULL DEFAULT 'constitution',
                       target_scope TEXT NOT NULL DEFAULT 'repo_dev',
                       summary TEXT,
                       fields TEXT,
                       clarifications TEXT,
                       clarification_answers TEXT,
                       staged_artifact TEXT,
                       staged_path TEXT,
                       eval_report TEXT,
                       apply_target TEXT,
                       cluster TEXT,
                       title TEXT NOT NULL,
                       body TEXT NOT NULL,
                       rationale TEXT,
                       confidence TEXT,
                       candidate_ids TEXT NOT NULL,
                       status TEXT NOT NULL DEFAULT 'proposed'
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_memprop_ctx "
                      "ON memory_proposal(context_id, status, created_at)")
            prop_cols = {r[1] for r in c.execute("PRAGMA table_info(memory_proposal)").fetchall()}
            # Two-gate lifecycle columns; idempotent on older DBs.
            for col in ("summary", "fields", "clarifications", "clarification_answers",
                        "staged_artifact", "staged_path", "eval_report"):
                if col not in prop_cols:
                    c.execute(f"ALTER TABLE memory_proposal ADD COLUMN {col} TEXT")
            # `title` is a short headline. `origin` is who created the row — the user-made vs
            # agent-made label.
            cols = {r[1] for r in c.execute("PRAGMA table_info(inbox)").fetchall()}
            if "title" not in cols:
                c.execute("ALTER TABLE inbox ADD COLUMN title TEXT")
            if "origin" not in cols:
                c.execute("ALTER TABLE inbox ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'")
            # One-time rename of legacy status value triaged -> pushed.
            c.execute("UPDATE inbox SET status='pushed' WHERE status='triaged'")
            # One-time: legacy SCALAR origins become JSON arrays. Idempotent — rows already
            # starting with `[` are left.
            for r in c.execute("SELECT id, origin FROM inbox").fetchall():
                o = r["origin"]
                if o is None or not str(o).strip().startswith("["):
                    c.execute("UPDATE inbox SET origin=? WHERE id=?",
                              (json.dumps(_norm_origins(o)), r["id"]))
            # `origin` supersedes the legacy `source` scalar. Fold a legacy `source='agent'` in
            # first, THEN drop.
            if "source" in cols:
                for r in c.execute("SELECT id, origin FROM inbox WHERE source='agent'").fetchall():
                    origins = _norm_origins(r["origin"])
                    if "agent" not in origins:
                        origins.append("agent")
                        c.execute("UPDATE inbox SET origin=? WHERE id=?",
                                  (json.dumps(origins), r["id"]))
                c.execute("ALTER TABLE inbox DROP COLUMN source")
            # `spawned_from` is the provenance edge a branch-off carries before push. NULL for
            # plain captures.
            if "spawned_from" not in cols:
                c.execute("ALTER TABLE inbox ADD COLUMN spawned_from TEXT")
            # Model and effort are chosen at CAPTURE and locked in at push. NULL = inherit the
            # repo/system default.
            if "model" not in cols:
                c.execute("ALTER TABLE inbox ADD COLUMN model TEXT")
            if "effort" not in cols:
                c.execute("ALTER TABLE inbox ADD COLUMN effort TEXT")
            # Capture is the one moment always in time to decide autopilot; the work-item route
            # accepts it pre-build only.
            if "autopilot" not in cols:
                c.execute("ALTER TABLE inbox ADD COLUMN autopilot INTEGER NOT NULL DEFAULT 1")
            # `work_kind` is the PROPOSED kind, NULL when nobody judged. Not named `kind` — that
            # already means the ticket's flavour.
            for col in ("vet_model", "vet_effort", "deputy_model", "deputy_effort"):
                if col not in cols:
                    c.execute(f"ALTER TABLE inbox ADD COLUMN {col} TEXT")
            if "work_kind" not in cols:
                c.execute("ALTER TABLE inbox ADD COLUMN work_kind TEXT")
            # Keyed off the PRESENCE of a legacy flavour, so a real owner-authored note survives a
            # second pass.
            if c.execute("SELECT 1 FROM inbox WHERE kind IN ('idea','todo','question') "
                         "LIMIT 1").fetchone():
                c.execute("UPDATE inbox SET kind='item'")

    # --- inbox CRUD -------------------------------------------------------------

    def add_inbox(self, context_id: str, text: str, kind: str = "item",
                  tag: str | None = None,
                  title: str | None = None, origin="user",
                  spawned_from: dict | None = None,
                  model: str | None = None, effort: str | None = None,
                  autopilot: bool = True, work_kind: str | None = None,
                  role_config: dict | None = None) -> dict:
        """File one inbox row. `work_kind` is the PROPOSED kind, validated LOUD — a dropped
        typo would read as "nobody proposed one"."""
        text = (text or "").strip()
        if not text:
            raise ValueError("empty inbox text")
        roles = {k: (v or None) for k, v in (role_config or {}).items() if k in _ROLE_COLS}
        if work_kind is not None:
            from .vocab.kind_profiles import KIND_PROFILES
            if work_kind not in KIND_PROFILES:
                raise ValueError(f"work_kind must be one of {sorted(KIND_PROFILES)}")
        if spawned_from is not None:
            if not isinstance(spawned_from, dict) or not spawned_from.get("item"):
                raise ValueError("spawned_from must be {item, relation[, note]}")
            if spawned_from.get("relation") not in _SPAWN_RELATIONS:
                raise ValueError(f"spawned_from.relation must be one of {sorted(_SPAWN_RELATIONS)}")
        kind = kind if kind in _KINDS else "note"
        origins = _norm_origins(origin)
        now = _now()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO inbox (context_id,kind,text,title,tag,status,origin,spawned_from,"
                "model,effort,autopilot,work_kind,vet_model,vet_effort,deputy_model,deputy_effort,"
                "created_at,updated_at) "
                "VALUES (?,?,?,?,?,'open',?,?,?,?,?,?,?,?,?,?,?,?)",
                (context_id, kind, text, (title or None), (tag or None),
                 json.dumps(origins),
                 json.dumps(spawned_from) if spawned_from else None,
                 (model or None), (effort or None), int(bool(autopilot)),
                 (work_kind or None),
                 *(roles.get(c_) for c_ in _ROLE_COLS),
                 now, now),
            )
            return self._get(c, cur.lastrowid)

    def append_inbox(self, item_id: int, addition: str, *, origin_add: str = "agent") -> dict | None:
        """APPEND to a row, never editing its text. Unions `origin_add`, so an augmented
        user item reads ['user','agent']."""
        addition = (addition or "").strip()
        if not addition:
            raise ValueError("empty append text")
        with self._conn() as c:
            row = c.execute("SELECT text, origin FROM inbox WHERE id=?", (item_id,)).fetchone()
            if row is None:
                return None
            origins = _norm_origins(row["origin"])
            if origin_add in _ORIGINS and origin_add not in origins:
                origins.append(origin_add)
            new_text = f"{row['text'].rstrip()}\n\n---\n{addition}"
            c.execute("UPDATE inbox SET text=?, origin=?, updated_at=? WHERE id=?",
                      (new_text, json.dumps(origins), _now(), item_id))
            return self._get(c, item_id)

    def push_inbox(self, item_id: int, work_item_id: str) -> dict | None:
        """Mark an inbox row as pushed to the workspace, recording the new work-item id."""
        with self._conn() as c:
            c.execute(
                "UPDATE inbox SET status='pushed', routed_to=?, updated_at=? WHERE id=?",
                (work_item_id, _now(), item_id),
            )
            return self._get(c, item_id)

    def list_inbox(self, context_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM inbox WHERE context_id=?"
                " ORDER BY (status!='open'), datetime(created_at) DESC, id DESC",
                (context_id,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["origin"] = _norm_origins(d.get("origin"))
                d["spawned_from"] = _parse_spawned_from(d.get("spawned_from"))
                d["autopilot"] = bool(d.get("autopilot"))
                out.append(d)
            return out

    def update_inbox(self, item_id: int, **fields) -> dict | None:
        sets = {k: v for k, v in fields.items()
                if k in {"kind", "text", "tag", "status", "routed_to", "title",
                         "model", "effort", "autopilot", "work_kind", *_ROLE_COLS}
                and v is not None}
        if sets.get("kind") not in _KINDS:
            sets.pop("kind", None)
        if sets.get("status") not in _STATUSES:
            sets.pop("status", None)
        if "work_kind" in sets:
            # An invalid value RAISES, never drops: NULL is a state, so a dropped typo reads as a
            # clear.
            from .vocab.kind_profiles import KIND_PROFILES
            if not sets["work_kind"]:
                sets["work_kind"] = None
            elif sets["work_kind"] not in KIND_PROFILES:
                raise ValueError(f"work_kind must be one of {sorted(KIND_PROFILES)}")
        if "autopilot" in sets:
            sets["autopilot"] = int(bool(sets["autopilot"]))
        # Stored as NULL, so "never set" and "set back to default" are one state, not two.
        for col in _ROLE_COLS:
            if sets.get(col) == "":
                sets[col] = None
        with self._conn() as c:
            if sets:
                sets["updated_at"] = _now()
                cols = ",".join(f"{k}=?" for k in sets)
                c.execute(f"UPDATE inbox SET {cols} WHERE id=?", (*sets.values(), item_id))
            return self._get(c, item_id)

    def delete_inbox(self, item_id: int) -> dict:
        with self._conn() as c:
            c.execute("DELETE FROM inbox WHERE id=?", (item_id,))
        return {"ok": True, "id": item_id}

    def purge_context(self, context_id: str) -> int:
        """Disconnect cleanup: drop a context's inbox queue and learning pool. `events`
        are deliberately kept."""
        n = 0
        with self._conn() as c:
            for table in ("inbox", "memory_candidate", "memory_proposal"):
                n += c.execute(f"DELETE FROM {table} WHERE context_id=?", (context_id,)).rowcount
        return n

    def _get(self, c: sqlite3.Connection, item_id: int) -> dict | None:
        r = c.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["origin"] = _norm_origins(d.get("origin"))
        d["spawned_from"] = _parse_spawned_from(d.get("spawned_from"))
        d["autopilot"] = bool(d.get("autopilot"))     # SQLite 0/1 → bool at the store boundary
        return d

    def get_inbox(self, item_id: int) -> dict | None:
        """Fetch one inbox row by id alone — for logging an event before a delete, where only
        the id is known."""
        with self._conn() as c:
            return self._get(c, item_id)


    # --- event log ---

    def log_event(self, context_id: str, kind: str, summary: str, *,
                  item_id: str | None = None, scope: str | None = None,
                  actor: str = "daemon", meta: dict | None = None) -> dict:
        """Append one event; `scope` auto-derives from `item_id`. Observers are notified AFTER
        the commit."""
        scope = scope or ("item" if item_id else "dev")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (context_id,scope,item_id,kind,actor,summary,meta,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (context_id, scope, item_id, kind, actor, summary,
                 (json.dumps(meta) if meta else None), _now()),
            )
            row = self._get_event(c, cur.lastrowid)
        _notify_event(row)
        return row

    def list_events(self, context_id: str, *, since: str | None = None,
                    until: str | None = None, scope: str | None = None,
                    item_id: str | None = None, limit: int = 200,
                    include_discarded: bool = False) -> list[dict]:
        """Selective read, never a full dump. `since`/`until` are ISO timestamps; newest first.

        `scope="repo"` is the ACTIVITY view: dev-native rows plus `REPO_MILESTONE_KINDS`. Soft-deleted
        re-run rows are hidden unless `include_discarded`."""
        where = ["context_id=?"]
        args: list = [context_id]
        if not include_discarded:
            where.append("discarded_at IS NULL")
        if item_id is not None:
            where.append("item_id=?")
            args.append(item_id)
        if scope == "repo":
            marks = ",".join("?" * len(REPO_MILESTONE_KINDS))
            where.append(f"(scope='dev' OR kind IN ({marks}))")
            args.extend(REPO_MILESTONE_KINDS)
        elif scope is not None:
            where.append("scope=?")
            args.append(scope)
        if since is not None:
            where.append("created_at>=?")
            args.append(since)
        if until is not None:
            where.append("created_at<=?")
            args.append(until)
        sql = (f"SELECT * FROM events WHERE {' AND '.join(where)}"
               f" ORDER BY datetime(created_at) DESC, id DESC LIMIT ?")
        args.append(int(limit))
        with self._conn() as c:
            return [self._row_event(r) for r in c.execute(sql, args).fetchall()]

    def discard_item_events(self, context_id: str, item_id: str, *, at: str) -> int:
        """SOFT-delete this item's dev events — the re-run's "start clean". Call
        BEFORE logging `item.rerun`, so that event survives."""
        with self._conn() as c:
            n = c.execute(
                "UPDATE events SET discarded_at=? WHERE context_id=? AND item_id=?"
                " AND discarded_at IS NULL", (at, context_id, str(item_id)),
            ).rowcount
            c.commit()
        return int(n or 0)

    def events_for_proposal(self, context_id: str, proposal_id: int, *, limit: int = 200) -> list[dict]:
        """The lifecycle trail for one learning proposal, oldest first. `proposal_id`
        lives in JSON `meta`, so filtering is in Python."""
        rows = self.list_events(context_id, scope="dev", limit=max(limit, 500))
        mine = [e for e in rows if isinstance(e.get("meta"), dict)
                and e["meta"].get("proposal_id") == proposal_id]
        mine.sort(key=lambda e: (e.get("created_at") or "", e.get("id") or 0))
        return mine[:limit]

    # Dev-activity events are historical trace and are never deleted.

    def _row_event(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["meta"] = json.loads(d["meta"]) if d.get("meta") else None
        return d

    def _get_event(self, c: sqlite3.Connection, event_id: int) -> dict:
        return self._row_event(c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone())

    # --- learning candidate pool ---

    def add_memory_candidate(self, context_id: str, signal: str, *,
                             source: str = "agent", form_hint: str | None = None,
                             rationale: str | None = None, scope_hint: str = "repo_dev",
                             origin_item_id: str | None = None,
                             origin_session_id: str | None = None,
                             evidence=None) -> dict:
        """File an operational-learning CANDIDATE — cheap and reversible, applying
        nothing. Operational only, never reference knowledge."""
        signal = (signal or "").strip()
        if not signal:
            raise ValueError("empty memory signal")
        source = source if source in _MEM_SOURCES else "agent"
        fh = form_hint if form_hint in _MEM_FORM_HINTS else None
        ev = None if evidence in (None, "") else (
            evidence if isinstance(evidence, str) else json.dumps(evidence))
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO memory_candidate"
                " (context_id,captured_at,source,origin_item_id,origin_session_id,"
                "  scope_hint,form_hint,signal,rationale,evidence,status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,'candidate')",
                (context_id, _now(), source, (origin_item_id or None),
                 (origin_session_id or None), (scope_hint or "repo_dev"), fh, signal,
                 (rationale or None), ev),
            )
            return self._get_candidate(c, cur.lastrowid)

    def list_memory_candidates(self, context_id: str, *, status: str | None = None,
                               limit: int = 200) -> list[dict]:
        """Read the candidate pool, newest first. Defaults to all statuses,
        filterable to 'candidate'."""
        where = ["context_id=?"]
        args: list = [context_id]
        if status is not None:
            where.append("status=?")
            args.append(status)
        sql = (f"SELECT * FROM memory_candidate WHERE {' AND '.join(where)}"
               f" ORDER BY datetime(captured_at) DESC, id DESC LIMIT ?")
        args.append(int(limit))
        with self._conn() as c:
            return [self._row_candidate(r) for r in c.execute(sql, args).fetchall()]

    def set_candidate_status(self, candidate_id: int, status: str) -> dict | None:
        """Advance a candidate through the lifecycle (processed/promoted/rejected/dropped)."""
        if status not in _MEM_CAND_STATUSES:
            raise ValueError(f"bad candidate status: {status}")
        with self._conn() as c:
            c.execute("UPDATE memory_candidate SET status=? WHERE id=?", (status, candidate_id))
            row = c.execute("SELECT * FROM memory_candidate WHERE id=?", (candidate_id,)).fetchone()
            return self._row_candidate(row) if row else None

    def delete_memory_candidates(self, context_id: str, ids: list[int]) -> int:
        """Hard-delete candidates, context-scoped. A rejected candidate is noise,
        not a log, so it never accretes in the pool."""
        ids = sorted({int(i) for i in (ids or [])})
        if not ids:
            return 0
        qs = ",".join("?" * len(ids))
        with self._conn() as c:
            cur = c.execute(
                f"DELETE FROM memory_candidate WHERE context_id=? AND id IN ({qs})",
                (context_id, *ids))
            return cur.rowcount

    def _row_candidate(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        if d.get("evidence"):
            try:
                d["evidence"] = json.loads(d["evidence"])
            except (ValueError, TypeError):
                pass  # plain-string evidence is fine — leave as-is
        return d

    def _get_candidate(self, c: sqlite3.Connection, cand_id: int) -> dict:
        return self._row_candidate(
            c.execute("SELECT * FROM memory_candidate WHERE id=?", (cand_id,)).fetchone())

    # --- learning proposals ---

    def add_memory_proposal(self, context_id: str, title: str, body: str, *,
                            candidate_ids: list[int], output_form: str = "constitution",
                            target_scope: str = "repo_dev", summary: str | None = None,
                            fields: dict | None = None, clarifications: list | None = None,
                            apply_target: str | None = None,
                            cluster: str | None = None, rationale: str | None = None,
                            confidence: str | None = None) -> dict:
        """File a distill PROPOSAL and mark its source candidates processed, in one transaction.

        Enums fall back to a safe default: distill's guess is advisory until the owner ratifies."""
        title, body = (title or "").strip(), (body or "").strip()
        if not title or not body:
            raise ValueError("proposal needs both title and body")
        ids = sorted({int(i) for i in (candidate_ids or [])})
        output_form = output_form if output_form in _MEM_OUTPUT_FORMS else "constitution"
        target_scope = target_scope if target_scope in _MEM_TARGET_SCOPES else "repo_dev"
        fields_j = json.dumps(fields) if fields else None
        clar_j = json.dumps(clarifications) if clarifications else None
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO memory_proposal"
                " (context_id,created_at,output_form,target_scope,summary,fields,clarifications,"
                "  apply_target,cluster,title,body,rationale,confidence,candidate_ids,status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'proposed')",
                (context_id, _now(), output_form, target_scope, (summary or None), fields_j, clar_j,
                 (apply_target or None), (cluster or None), title, body, (rationale or None),
                 (confidence or None), json.dumps(ids)),
            )
            if ids:
                qs = ",".join("?" * len(ids))
                c.execute(
                    f"UPDATE memory_candidate SET status='processed'"
                    f" WHERE context_id=? AND id IN ({qs})",
                    (context_id, *ids),
                )
            return self._get_proposal(c, cur.lastrowid)

    def merge_memory_proposal(self, proposal_id: int, context_id: str, *,
                              add_candidate_ids: list[int], title: str | None = None,
                              body: str | None = None, summary: str | None = None,
                              fields: dict | None = None, confidence: str | None = None,
                              cluster: str | None = None) -> dict | None:
        """Fold new candidates into an existing open proposal, so a re-capture enriches rather than
        duplicates.

        A `drafted` target reverts to `proposed` and drops its staged artifact."""
        add = sorted({int(i) for i in (add_candidate_ids or [])})
        with self._conn() as c:
            row = c.execute("SELECT * FROM memory_proposal WHERE id=? AND context_id=?",
                            (proposal_id, context_id)).fetchone()
            if row is None:
                return None
            cur_status = row["status"]
            if cur_status in ("published", "rejected", "dropped", "superseded"):
                raise ValueError(f"proposal #{proposal_id} is {cur_status} — not open to merge into")
            if cur_status == "writing":
                raise ValueError(f"proposal #{proposal_id} is mid-forge — merge after it settles")
            try:
                existing = json.loads(row["candidate_ids"]) if row["candidate_ids"] else []
            except (ValueError, TypeError):
                existing = []
            merged_ids = sorted({*(int(i) for i in existing), *add})
            # A drafted artifact no longer reflects the fuller candidate set — revert for re-forge.
            reforge = cur_status == "drafted"
            sets = ["candidate_ids=?"]
            params: list = [json.dumps(merged_ids)]
            if title:
                sets.append("title=?"); params.append(title.strip())
            if body:
                sets.append("body=?"); params.append(body.strip())
            if summary is not None:
                sets.append("summary=?"); params.append(summary or None)
            if fields is not None:
                sets.append("fields=?"); params.append(json.dumps(fields) if fields else None)
            if confidence:
                sets.append("confidence=?"); params.append(confidence)
            if cluster is not None:
                sets.append("cluster=?"); params.append(cluster or None)
            if reforge:
                sets += ["status='proposed'", "staged_artifact=NULL",
                         "staged_path=NULL", "eval_report=NULL"]
            params.append(proposal_id)
            c.execute(f"UPDATE memory_proposal SET {', '.join(sets)} WHERE id=?", params)
            if add:
                qs = ",".join("?" * len(add))
                c.execute(
                    f"UPDATE memory_candidate SET status='processed'"
                    f" WHERE context_id=? AND id IN ({qs})",
                    (context_id, *add),
                )
            prop = self._get_proposal(c, proposal_id)
            prop["reforged"] = reforge
            return prop

    def list_memory_proposals(self, context_id: str, *, status: str | None = None,
                              limit: int = 200) -> list[dict]:
        """Read the proposal pool, newest first. Defaults to all statuses,
        filterable to 'proposed'."""
        where = ["context_id=?"]
        args: list = [context_id]
        if status is not None:
            where.append("status=?")
            args.append(status)
        sql = (f"SELECT * FROM memory_proposal WHERE {' AND '.join(where)}"
               f" ORDER BY datetime(created_at) DESC, id DESC LIMIT ?")
        args.append(int(limit))
        with self._conn() as c:
            return [self._row_proposal(r) for r in c.execute(sql, args).fetchall()]

    def get_memory_proposal(self, proposal_id: int) -> dict | None:
        """Fetch a single proposal by id (the owner gate resolves one row to act on)."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM memory_proposal WHERE id=?", (proposal_id,)).fetchone()
            return self._row_proposal(row) if row else None

    def set_proposal_status(self, proposal_id: int, status: str) -> dict | None:
        """Advance a proposal through the owner gate (accepted/rejected/superseded)."""
        if status not in _MEM_PROP_STATUSES:
            raise ValueError(f"bad proposal status: {status}")
        with self._conn() as c:
            c.execute("UPDATE memory_proposal SET status=? WHERE id=?", (status, proposal_id))
            row = c.execute("SELECT * FROM memory_proposal WHERE id=?", (proposal_id,)).fetchone()
            return self._row_proposal(row) if row else None

    def _row_proposal(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        try:
            d["candidate_ids"] = json.loads(d["candidate_ids"]) if d.get("candidate_ids") else []
        except (ValueError, TypeError):
            d["candidate_ids"] = []
        # JSON columns → parsed structures, tolerating plain or absent values on old rows.
        for col in ("fields", "clarifications", "clarification_answers", "eval_report"):
            if d.get(col):
                try:
                    d[col] = json.loads(d[col])
                except (ValueError, TypeError):
                    pass
        return d

    def stage_proposal_artifact(self, proposal_id: int, *, staged_artifact: str,
                                staged_path: str | None = None, eval_report: dict | None = None,
                                status: str = "drafted") -> dict | None:
        """Write phase → DB-stage the rendered artifact and move to `drafted`.
        Disk stays untouched until publish."""
        if status not in _MEM_PROP_STATUSES:
            raise ValueError(f"bad proposal status: {status}")
        er = json.dumps(eval_report) if eval_report is not None else None
        with self._conn() as c:
            c.execute(
                "UPDATE memory_proposal SET staged_artifact=?, staged_path=?, eval_report=?, status=?"
                " WHERE id=?",
                (staged_artifact, (staged_path or None), er, status, proposal_id),
            )
            return self._get_proposal(c, proposal_id)

    def update_staged_artifact(self, proposal_id: int, content: str) -> dict | None:
        """Owner edits the staged artifact before publishing. Status stays
        `drafted`; publish writes whatever is here."""
        with self._conn() as c:
            c.execute("UPDATE memory_proposal SET staged_artifact=? WHERE id=?",
                      (content, proposal_id))
            return self._get_proposal(c, proposal_id)

    def set_proposal_clarification_answers(self, proposal_id: int, answers) -> dict | None:
        """Gate 1 → store the owner's answers to distill's batch clarifying questions."""
        aj = answers if isinstance(answers, str) else json.dumps(answers)
        with self._conn() as c:
            c.execute("UPDATE memory_proposal SET clarification_answers=? WHERE id=?",
                      (aj, proposal_id))
            return self._get_proposal(c, proposal_id)

    def _get_proposal(self, c: sqlite3.Connection, prop_id: int) -> dict:
        return self._row_proposal(
            c.execute("SELECT * FROM memory_proposal WHERE id=?", (prop_id,)).fetchone())

    # --- one-time migration from a legacy inbox.md ------------------------------

    def migrate_inbox_md(self, context_id: str, md_path: Path) -> int:
        """Import a legacy inbox.md once; `###` headers become tags. No-op if rows exist
        or the file is missing."""
        md_path = Path(md_path)
        if not md_path.exists():
            return 0
        with self._conn() as c:
            if c.execute("SELECT COUNT(*) FROM inbox WHERE context_id=?", (context_id,)).fetchone()[0]:
                return 0
        tag, n, now = None, 0, _now()
        with self._conn() as c:
            for raw in md_path.read_text(encoding="utf-8").splitlines():
                s = raw.strip()
                if s.startswith("### "):
                    head = s[4:].split("(")[0].split("—")[0].lower()
                    tag = ("b2" if ("slack" in head or "cross-surface" in head)
                           else "harness" if "harness" in head
                           else "surfaces" if "surface" in head
                           else "housekeeping" if "housekeep" in head
                           else _slug(head))
                    continue
                if s.startswith("#"):
                    continue
                m = _LINE.match(s)
                if not m:
                    continue
                mark, d, kind, text = m.groups()
                text = (text or "").strip()
                if not text:
                    continue
                c.execute(
                    "INSERT INTO inbox (context_id,kind,text,tag,status,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (context_id, kind if kind in _KINDS else "note", text, tag,
                     "pushed" if mark == "x" else "open", d or now[:10], now),
                )
                n += 1
        return n
