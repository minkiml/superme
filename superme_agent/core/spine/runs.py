"""Runs — one execution each, started, bumped and finished, under a per-item lock."""

import json
import sqlite3

from .common import _RUN_STATUSES, _now, _opens_a_file


class RunOps:
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
        """How many SUBAGENTS this item's runs at `phase` spawned.

        Counts kernel-written `run_event` rows, so a fan-out cannot be claimed without one."""
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

        A proxy: it proves a brief too short to carry a bar, never that a long one was right."""
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
        """Every subagent BRIEF this item's runs sent at `phase`, in spawn order.

        The brief is the whole channel to a spawned worker. Spawns predating storage are absent."""
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
        """How many times this item's runs at `phase` read a path containing `needle`.

        Counts the ACT, not the tool: `cat <guide>` is a read, naming a path is not."""
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
        """When this item's most recent FINISHED run at `phase` ended.

        The cutoff for what changed since this thread last ran, and it excludes the asking run."""
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
        """Close the item's running row, keeping the accumulated live token sum.

        `ctx_pct` is the authoritative end-of-turn fill and overwrites the last estimate."""
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
            if ctx_pct is not None:  # the authoritative fill, overriding the live-bump estimate
                sets.append("ctx_pct=?")
                args.append(int(ctx_pct))
            if outcome:
                sets.append("outcome=?")
                args.append(str(outcome))
            if session_id:  # so `session_fate` labeling can reach this row
                sets.append("session_id=?")
                args.append(str(session_id))
            args.append(row["id"])
            c.execute(f"UPDATE run SET {', '.join(sets)} WHERE id=?", args)
            self._finish_usage_apply(c, row["id"], usage)
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
        """Close out an item's in-flight run rows, keeping every row.

        THE ROW HALF ONLY. Disposing of an item wants `stop_item_work`, which cancels the task first."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE run SET status='aborted', ended_at=? WHERE repo_id=? AND item_id=? AND status='running'",
                (_now(), repo_id, item_id))
            return cur.rowcount
