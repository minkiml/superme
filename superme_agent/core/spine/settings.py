"""Owner-tunable runtime settings: the loop budget, the deputy, learning, sweeps."""

import json

from .common import _now


class SettingsOps:
    # --- build⟷vet loop ---

    # Token budget is the PRIMARY breaker: measured spend, not a cycle count. An item's own
    # `loop_budget` wins over this default.
    DEFAULT_LOOP_BUDGET = 500_000

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

    # --- delegated deputy authority ---

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

    # --- learning master switch --------------------------------------------------
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

    # --- autopilot concurrency: the per-project launch breaker ---
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

    # --- deputy: the autopilot gate judge ---

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
        from ..models import model_family
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

    # --- compaction runtime tuning ---

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
