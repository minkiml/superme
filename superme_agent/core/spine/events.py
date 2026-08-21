"""The per-run trail: prompt, reply, and every tool, skill and sub-agent call."""

import json

from .common import _duration_ms, _now


class EventOps:
    # `run_artifact` has no writer any more; `run_event` supersedes it.
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
            toks = self._display_tokens(r)
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
