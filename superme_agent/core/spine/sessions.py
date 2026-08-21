"""Sessions — a resumable conversation, its stamps, and what it is centred on."""

import sqlite3

from .common import _norm, _now


class SessionOps:
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
        """The stored (kind, subject_run_id), or None. An unstamped session has a NULL `kind`, and the
        caller derives it."""
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

    # --- capture-sweep watermark ------------------------------------------------
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
