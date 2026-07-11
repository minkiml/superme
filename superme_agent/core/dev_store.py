"""DevStore — the dev cockpit's operational SQLite store.

Per decision D-013, durable *knowledge* (plan/decisions/learnings/design) stays in
markdown files, while *operational/queue/run* state lives here in a host-app SQLite DB
keyed by context_id. Today that's the inbox triage queue (D-014); agent runs, sessions,
evals, checkpoints, and approvals will join it as the cockpit grows active.

Localhost, single-owner: short-lived connections per call are plenty.
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_KINDS = {"note", "idea", "todo", "question"}
# open = awaiting push · pushed = promoted to a work-item.
# ("pushed" replaces the old "triaged" — the inbox→workspace gesture is "push".)
# Dropping an item is a HARD DELETE (delete_inbox), not a soft status — so there is no
# "dropped" state. (An agent-proposed row that's declined is deleted + its origin signalled.)
_STATUSES = {"open", "pushed"}
# An inbox item's origin is a LIST — an item can accrue multiple origins over its life. It starts
# with whoever created it ('user' or 'agent') and gains 'agent' when the itemize skill appends a new
# discussion onto an existing item (so a user-made item touched by the agent reads ['user','agent']).
# Stored as a JSON array in the TEXT column; legacy scalar values are coerced on read + migrated once.
_ORIGINS = {"user", "agent"}


def _norm_origins(value, *, default: str = "user") -> list[str]:
    """Coerce a stored/incoming origin (JSON array, legacy scalar, list, or None) to a de-duplicated,
    order-preserving list of valid origins. Empty/unknown falls back to [default]."""
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
# Operational-learning CANDIDATE pool (WI-8; was tier-C "memory") — the capture end of
# capture→distill→ratify→write→publish. A candidate is a rich, reversible OPERATIONAL observation
# (behavior-shaping only — never reference knowledge) the owner reviews later; nothing is applied
# here. See general_docs/learning-workflow-spec.md.
_MEM_SOURCES = {"agent", "user"}          # who captured (user-injected still gets reviewed)
# The operational form the agent GUESSES a candidate should become (advisory; distill decides).
_MEM_FORM_HINTS = {"constitution", "skill", "agent"}
_MEM_KIND_HINTS = _MEM_FORM_HINTS         # back-compat alias (the old kind_hint column)
_MEM_CAND_STATUSES = {"candidate", "processed", "promoted", "rejected", "dropped"}
# PROPOSAL — distill's output (the typed, classified consolidation the owner ratifies at gate 1).
# output_form = WHICH operational artifact it becomes; target_scope = WHERE it lands. Both are
# distill's guess — the owner can override at ratify. The renovation (§4.11.5) collapsed the old
# {fact, contract, core_candidate, recall_type} model: self-learning is OPERATIONAL and emits only
# {constitution, skill, agent} (constitution = the one coarse behavior-governing bucket).
_MEM_OUTPUT_FORMS = {"constitution", "skill", "agent"}
_MEM_TARGET_SCOPES = {"repo_dev", "universal_dev", "core"}
# Two-gate lifecycle: proposed (distill) → writing (gate-1 approved, write run in flight) →
# drafted (write phase staged the artifact + eval) → published (gate-2 approved + written to disk).
# Terminal: rejected/dropped/superseded.
_MEM_PROP_STATUSES = {"proposed", "writing", "drafted", "published", "rejected", "dropped",
                      "superseded", "retired"}  # retired = a published artifact the owner deleted
_LINE = re.compile(r"^- \[( |x)\]\s*(\d{4}-\d{2}-\d{2})?\s*(\w+)?:?\s*(.*)$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(s: str) -> str | None:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-") or None


class DevStore:
    """Operational state for the dev dashboard. Currently the inbox triage queue."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

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
                       source TEXT NOT NULL DEFAULT 'human',
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_inbox_ctx ON inbox(context_id, status)")
            # Agent-run telemetry now lives in the system spine's `run` table (WI-4). Drop the
            # legacy `runs` table from any pre-renovation .dev.db (its rows were migrated by WI-2).
            c.execute("DROP TABLE IF EXISTS runs")
            # The event LOG (PRD §4.9) — the append-only activity firehose, orchestrator-written.
            # DB-first (markdown log views render FROM this), so "what happened yesterday?" is a
            # date-filtered WHERE, not a prose scan. `scope` (item|dev|global) + `item_id`
            # (NULL = dev-native: inbox triage / merges / cleanups, attached to no work-item) give
            # the containment read: item view = WHERE item_id=X; dev view = all the repo's rows.
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
                       created_at TEXT NOT NULL
                   )"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS idx_events_ctx ON events(context_id, created_at)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_events_item ON events(context_id, item_id)")
            # Operational-learning CANDIDATE pool (WI-8) — operational/churny, so DB not files
            # (same rationale as inbox/events). The capture sweep files a RICH row here: `signal` = the
            # operational statement (what to do), `rationale` = why it matters / the trigger,
            # `evidence` = concrete instance(s) + a session/work-item pointer, `form_hint` = the
            # agent's guess at {constitution|skill|agent}. The bar (dev-charter): self-sufficient
            # for a fresh-context distill. distill consolidates these into typed proposals.
            c.execute(
                """CREATE TABLE IF NOT EXISTS memory_candidate (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       context_id TEXT NOT NULL,
                       captured_at TEXT NOT NULL,
                       source TEXT NOT NULL DEFAULT 'agent',
                       origin_item_id TEXT,
                       origin_session_id TEXT,
                       scope_hint TEXT NOT NULL DEFAULT 'dev',
                       kind_hint TEXT,
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
            for col in ("form_hint", "rationale"):  # WI-8 richer candidate (idempotent on old DBs)
                if col not in cand_cols:
                    c.execute(f"ALTER TABLE memory_candidate ADD COLUMN {col} TEXT")
            # Operational-learning PROPOSAL pool (WI-8) — distill consolidates candidates into
            # these TYPED, classified rows; the owner ratifies at gate 1, the write phase stages
            # the rendered artifact (+ eval report for skill/agent), the owner reviews at gate 2,
            # publish writes it to disk. Columns: `summary` (purpose·usage·why-raised), `fields`
            # (JSON of form-specific fields), `clarifications` (JSON batch Qs distill attaches),
            # `clarification_answers` (JSON, filled at gate 1), `staged_artifact` + `staged_path`
            # (the write phase's rendered final artifact + its publish home — DB-staged; disk holds
            # only PUBLISHED content), `eval_report` (JSON metrics+verdict for skill/agent).
            # `cluster` groups related-but-not-merged proposals (conservative dedup). `candidate_ids`
            # is the JSON set of sources, marked 'processed' when the proposal is filed.
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
            # WI-8 two-gate lifecycle columns (idempotent on pre-WI-8 DBs).
            for col in ("summary", "fields", "clarifications", "clarification_answers",
                        "staged_artifact", "staged_path", "eval_report"):
                if col not in prop_cols:
                    c.execute(f"ALTER TABLE memory_proposal ADD COLUMN {col} TEXT")
            # Columns added after v1 (idempotent):
            #   `title`  — a short headline (now entered manually on capture).
            #   `origin` — who created the row: 'user' (manual capture) | 'agent' (a
            #              branch-off proposal). The "user-made vs agent-made" label.
            cols = {r[1] for r in c.execute("PRAGMA table_info(inbox)").fetchall()}
            if "title" not in cols:
                c.execute("ALTER TABLE inbox ADD COLUMN title TEXT")
            if "origin" not in cols:
                c.execute("ALTER TABLE inbox ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'")
            # One-time rename of legacy status value triaged -> pushed.
            c.execute("UPDATE inbox SET status='pushed' WHERE status='triaged'")
            # One-time: migrate legacy SCALAR origins ('user'/'agent') to JSON arrays, so the column
            # is uniformly a list going forward. Idempotent — rows already starting with '[' are left.
            for r in c.execute("SELECT id, origin FROM inbox").fetchall():
                o = r["origin"]
                if o is None or not str(o).strip().startswith("["):
                    c.execute("UPDATE inbox SET origin=? WHERE id=?",
                              (json.dumps(_norm_origins(o)), r["id"]))

    # --- inbox CRUD -------------------------------------------------------------

    def add_inbox(self, context_id: str, text: str, kind: str = "note",
                  tag: str | None = None, source: str = "human",
                  title: str | None = None, origin="user") -> dict:
        text = (text or "").strip()
        if not text:
            raise ValueError("empty inbox text")
        kind = kind if kind in _KINDS else "note"
        origins = _norm_origins(origin)
        now = _now()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO inbox (context_id,kind,text,title,tag,status,origin,source,created_at,updated_at)"
                " VALUES (?,?,?,?,?,'open',?,?,?,?)",
                (context_id, kind, text, (title or None), (tag or None),
                 json.dumps(origins), source, now, now),
            )
            return self._get(c, cur.lastrowid)

    def append_inbox(self, item_id: int, addition: str, *, origin_add: str = "agent") -> dict | None:
        """APPEND to an existing inbox item — never edits its existing text. Adds `addition` under a
        divider and unions `origin_add` into the item's origin list (so a user-made item the itemize
        skill later augments reads ['user','agent']). Bumps updated_at. Used when the itemize skill
        finds the work already ticketed but the new discussion adds something worth keeping."""
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
                d["origin"] = _norm_origins(d.get("origin"))  # always a list to callers
                out.append(d)
            return out

    def update_inbox(self, item_id: int, **fields) -> dict | None:
        sets = {k: v for k, v in fields.items()
                if k in {"kind", "text", "tag", "status", "routed_to", "title"} and v is not None}
        if sets.get("kind") not in _KINDS:
            sets.pop("kind", None)
        if sets.get("status") not in _STATUSES:
            sets.pop("status", None)
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

    def _get(self, c: sqlite3.Connection, item_id: int) -> dict | None:
        r = c.execute("SELECT * FROM inbox WHERE id=?", (item_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["origin"] = _norm_origins(d.get("origin"))  # always a list to callers
        return d

    def get_inbox(self, item_id: int) -> dict | None:
        """Fetch a single inbox row by id alone (no context needed) — used to log an event
        before a delete, where the caller only has the row id."""
        with self._conn() as c:
            return self._get(c, item_id)

    # Agent-run telemetry moved to the system spine's `run` table (WI-4). The old DevStore
    # `runs` table + its start_run/finish_run/run_stats/delete_runs methods are gone; the data
    # was migrated by the WI-2 importer and the table is dropped in _init below.

    # --- event log (PRD §4.9) ---------------------------------------------------

    def log_event(self, context_id: str, kind: str, summary: str, *,
                  item_id: str | None = None, scope: str | None = None,
                  actor: str = "daemon", meta: dict | None = None) -> dict:
        """Append one event to the log. `scope` auto-derives when omitted: an event with an
        `item_id` is item-scoped, otherwise dev-native (item_id NULL). Append-only — never
        edited. Returns the stored row (meta decoded)."""
        scope = scope or ("item" if item_id else "dev")
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO events (context_id,scope,item_id,kind,actor,summary,meta,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (context_id, scope, item_id, kind, actor, summary,
                 (json.dumps(meta) if meta else None), _now()),
            )
            return self._get_event(c, cur.lastrowid)

    def list_events(self, context_id: str, *, since: str | None = None,
                    until: str | None = None, scope: str | None = None,
                    item_id: str | None = None, limit: int = 200) -> list[dict]:
        """Selective read — never a full dump. item view = WHERE item_id=X; dev view = all the
        repo's rows (dev-native + every item's). `since`/`until` are ISO timestamps (lexical
        compare is correct for ISO-UTC). Newest first."""
        where = ["context_id=?"]
        args: list = [context_id]
        if item_id is not None:
            where.append("item_id=?")
            args.append(item_id)
        if scope is not None:
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

    def events_for_proposal(self, context_id: str, proposal_id: int, *, limit: int = 200) -> list[dict]:
        """The lifecycle trail for one learning proposal — dev events whose `meta.proposal_id` matches
        (approve → write.start/end → artifact_edited → published/rejected/dropped). Oldest first, so it
        reads as a timeline. proposal_id lives in the JSON `meta`, so we filter in Python (the pool is
        small + context-scoped)."""
        rows = self.list_events(context_id, scope="dev", limit=max(limit, 500))
        mine = [e for e in rows if isinstance(e.get("meta"), dict)
                and e["meta"].get("proposal_id") == proposal_id]
        mine.sort(key=lambda e: (e.get("created_at") or "", e.get("id") or 0))
        return mine[:limit]

    def delete_events(self, context_id: str, item_id: str) -> int:
        """Drop an item's events when the work-item is hard-deleted. Dev-native events
        (item_id NULL) are never touched by this."""
        with self._conn() as c:
            cur = c.execute("DELETE FROM events WHERE context_id=? AND item_id=?", (context_id, item_id))
            return cur.rowcount

    def _row_event(self, r: sqlite3.Row) -> dict:
        d = dict(r)
        d["meta"] = json.loads(d["meta"]) if d.get("meta") else None
        return d

    def _get_event(self, c: sqlite3.Connection, event_id: int) -> dict:
        return self._row_event(c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone())

    # --- tier-C memory candidate pool (PRD §4.10) -------------------------------

    def add_memory_candidate(self, context_id: str, signal: str, *,
                             source: str = "agent", form_hint: str | None = None,
                             rationale: str | None = None, scope_hint: str = "dev",
                             origin_item_id: str | None = None,
                             origin_session_id: str | None = None,
                             evidence=None, kind_hint: str | None = None) -> dict:
        """File an operational-learning CANDIDATE (WI-8) — the capture end. Cheap and reversible:
        it applies nothing, just queues a RICH operational observation distill consolidates and the
        owner gates. `signal` = the operational statement; `rationale` = why it matters / the
        trigger; `evidence` = concrete instance(s) + a pointer (never bulk content); `form_hint` =
        the agent's {constitution|skill|agent} guess; `scope_hint` = {repo_dev|universal_dev|core}
        (legacy 'dev'/'core' tolerated). Operational only — never reference knowledge."""
        signal = (signal or "").strip()
        if not signal:
            raise ValueError("empty memory signal")
        source = source if source in _MEM_SOURCES else "agent"
        # `form_hint` supersedes the old `kind_hint`; accept either, store in form_hint.
        fh = form_hint or kind_hint
        fh = fh if fh in _MEM_FORM_HINTS else None
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
        """Read the candidate pool (newest first). The processor + a future Manage-Harness
        view consume this; defaults to all statuses, filterable to 'candidate' (un-processed)."""
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
        """Hard-delete candidates from the pool (context-scoped for safety). Used by distill to
        permanently drop what fails its gate — a rejected candidate is noise, not a log, so it's
        removed outright rather than left to accrete in the pool (quality over quantity). Returns
        the number of rows deleted. (Candidates are pre-ratification pool items, distinct from the
        run/transcript telemetry that is never deleted.)"""
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

    # --- tier-C memory proposals (PRD §4.10.2) ----------------------------------

    def add_memory_proposal(self, context_id: str, title: str, body: str, *,
                            candidate_ids: list[int], output_form: str = "constitution",
                            target_scope: str = "repo_dev", summary: str | None = None,
                            fields: dict | None = None, clarifications: list | None = None,
                            apply_target: str | None = None,
                            cluster: str | None = None, rationale: str | None = None,
                            confidence: str | None = None) -> dict:
        """File a distill PROPOSAL and atomically mark its source candidates 'processed'.

        One transaction so a proposal and the candidates it consumes never drift: a candidate is
        'processed' iff a proposal references it. `candidate_ids` must belong to this context
        (cross-context ids ignored on the status flip). `output_form` ∈ {constitution, skill, agent};
        `summary` = purpose·usage·why-raised; `fields` = the form-specific structured fields (JSON);
        `clarifications` = batch questions the owner answers at gate 1 (JSON list). Enums fall back
        to a safe default — distill's guess is advisory; the owner re-classifies at ratify.
        """
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
        """Fold new candidate(s) into an EXISTING open proposal — CROSS-RUN consolidation. The same
        learning captured again in a later session enriches the standing proposal instead of minting
        a parallel one (the #90/#99 duplicate class). Unions candidate_ids, optionally rewrites the
        consolidated content (distill supplies the enriched title/body/summary/fields), and marks the
        new candidates 'processed'. If the target was already `drafted` (forged), it reverts to
        `proposed` and clears the staged artifact + eval — the substance changed, so it must be
        re-authored. One transaction so the proposal and its candidate set never drift.

        Returns the updated proposal (with a transient `reforged` flag), None if there's no such
        proposal in this context, and raises ValueError if the target isn't open to merge
        (published/rejected/dropped/superseded) or is mid-forge (`writing`)."""
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
            # A forged (drafted) proposal's staged artifact no longer reflects the fuller candidate
            # set → revert to proposed for re-forge, clearing the now-stale artifact + eval.
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
        """Read the proposal pool (newest first). The owner-review (sub-stage 3) consumes this;
        defaults to all statuses, filterable to 'proposed' (awaiting the owner gate)."""
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
        # WI-8 JSON columns → parsed structures (tolerate plain/None on old rows).
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
        """Write phase → DB-stage the rendered complete artifact (+ optional eval report) and move
        the proposal to `drafted` (awaiting gate 2). Disk stays untouched until publish."""
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
        """Owner edits the staged artifact at gate 2 before publishing (refine-then-publish). Only
        the staged content changes — status stays `drafted`; publish then writes whatever is here."""
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
        """Import a legacy inbox.md once (its `###` section headers become `tag`s).

        No-op if the context already has rows or the file is missing. Returns # imported.
        """
        md_path = Path(md_path)
        if not md_path.exists():
            return 0
        with self._conn() as c:
            if c.execute("SELECT COUNT(*) FROM inbox WHERE context_id=?", (context_id,)).fetchone()[0]:
                return 0
        tag, n, now = None, 0, _now()
        with self._conn() as c:
            for raw in md_path.read_text().splitlines():
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
                    "INSERT INTO inbox (context_id,kind,text,tag,status,source,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (context_id, kind if kind in _KINDS else "note", text, tag,
                     "triaged" if mark == "x" else "open", "human", d or now[:10], now),
                )
                n += 1
        return n
