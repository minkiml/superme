"""Which model and reasoning effort a turn resolves to, per role, repo and agent."""

from .common import _now


class ModelOps:
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
        from ..vocab.models import DEFAULT_MODEL, normalize_model
        return normalize_model(self.system_config().default_model) or DEFAULT_MODEL

    def effective_model(self, repo_id: str, *, per_call: str | None = None,
                        item_model: str | None = None) -> str:
        """THE model-precedence resolver: per_call → item_model → this repo's default →
        the system default. `per_call` never writes the repo default."""
        return (per_call or item_model or self.get_model_override(repo_id)
                or self.effective_system_model())

    def role_model(self, repo_id: str, role: str, *, item_model: str | None = None) -> str:
        """The model a named ROLE runs on: the item's pick, this repo's tier, then the floor.

        The project default is absent: a judge inheriting the worker's tier is no check."""
        return item_model or self.get_model_override(repo_id, role) or self.effective_system_model()

    # --- per-agent model (the autonomous background sub-agents; owner-tunable) ------------------

    # SOURCE OF TRUTH = each sub-agent's own `.md` frontmatter. The code preset is the fallback.
    @staticmethod
    def _agent_md_path(feature: str):
        from ..vocab.models import AGENT_MD_NAME
        from ...paths import DEV_PLUGIN_DIR
        name = AGENT_MD_NAME.get(feature)
        return (DEV_PLUGIN_DIR / "agents" / f"{name}.md") if name else None

    def _agent_file_model(self, feature: str) -> str | None:
        """The raw `model:` from the sub-agent's `.md` frontmatter (None if absent/unreadable)."""
        from ..operational import parse_frontmatter
        path = self._agent_md_path(feature)
        if not path or not path.is_file():
            return None
        try:
            meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return meta.get("model") or None

    def resolve_agent_model(self, feature: str) -> str:
        """The concrete, latest model a background sub-agent runs on: its `.md` tier alias
        resolved here, else the code preset."""
        from ..vocab.models import agent_model, track_to_latest
        return track_to_latest(self._agent_file_model(feature)) or agent_model(feature)

    def set_agent_model(self, feature: str, model: str | None) -> None:
        """Write a sub-agent's model into its `.md` frontmatter as a TIER ALIAS, so a
        MODEL_TIERS bump needs no file rewrite."""
        from ..vocab.models import AGENT_MODELS, model_family
        from ..operational import set_frontmatter_field
        path = self._agent_md_path(feature)
        if not path or not path.is_file():
            raise ValueError(f"no agent .md for feature '{feature}'")
        alias = model_family(model) or AGENT_MODELS.get(feature) or "sonnet"
        set_frontmatter_field(path, "model", alias)

    _AGENT_EFFORT_DEFAULT = "medium"
    _AGENT_EFFORTS = ("low", "medium", "high")

    def _agent_file_effort(self, feature: str) -> str | None:
        """The raw `effort:` from the sub-agent's `.md` frontmatter (None if absent/unreadable)."""
        from ..operational import parse_frontmatter
        path = self._agent_md_path(feature)
        if not path or not path.is_file():
            return None
        try:
            meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
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
        from ..operational import set_frontmatter_field
        path = self._agent_md_path(feature)
        if not path or not path.is_file():
            raise ValueError(f"no agent .md for feature '{feature}'")
        eff = (effort or "").strip().lower()
        set_frontmatter_field(path, "effort", eff if eff in self._AGENT_EFFORTS else self._AGENT_EFFORT_DEFAULT)

    def reconcile_model_overrides(self) -> None:
        """Normalize picker overrides to their TIER ALIAS, so old concrete picks
        auto-track a MODEL_TIERS bump. Idempotent."""
        from ..vocab.models import model_family
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
        from ..vocab.models import AGENT_MODEL_FEATURES, model_family
        from ..operational import set_frontmatter_field
        for feat in AGENT_MODEL_FEATURES:
            cur = self._agent_file_model(feat)
            alias = model_family(cur)
            path = self._agent_md_path(feat)
            if alias and cur and alias != cur and path and path.is_file():
                set_frontmatter_field(path, "model", alias)

    def agent_model_config(self) -> list[dict]:
        """The tunable background sub-agents in display order: label, scope, tracked tier,
        and the concrete it resolves to."""
        from ..vocab.models import (AGENT_MODEL_FEATURES, AGENT_MODEL_LABELS, AGENT_MODEL_SCOPE,
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
