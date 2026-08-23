"""The repo registry, and the live status derived from what its runs are doing."""

from pathlib import Path

import yaml

from ..vocab.kind_profiles import AGENT_THREAD_KINDS
from .common import LEARNING_FEATURES, _norm, _now, log
from .config import (REVIEW_MODES, REVIEW_MODE_DEFAULT, RepoConfig, SystemConfig, load_repos,
                     load_system_config)


class RepoOps:
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
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        if "repos:" not in text:                       # fresh/empty file → start the mapping
            text = (text.rstrip() + "\n\nrepos:\n") if text.strip() else "repos:\n"
        elif not text.endswith("\n"):
            text += "\n"
        path.write_text(text + block, encoding="utf-8")
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
        # An empty string clears a field, and clearing means dropping its line.
        patch = {k: (v.strip() if isinstance(v, str) else v) or None for k, v in fields.items()}
        # A field set back to its DEFAULT is stored as absence, so setting and unsetting leaves
        # the file byte-identical.
        if patch.get("review_mode") == REVIEW_MODE_DEFAULT:
            patch["review_mode"] = None
        def _line(key: str, val) -> str:
            # A one-key mapping, not a bare scalar: safe_dump of a scalar emits a whole document.
            return "    " + yaml.safe_dump({key: val}, sort_keys=False).strip() + "\n"

        path = Path(self._repos_config_path)
        text = path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):   # else an appended key joins the last line
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
            seen: set[str] = set()
            while i < n and lines[i].startswith("    "):
                key = lines[i].split(":", 1)[0].strip()
                if key in patch:
                    seen.add(key)
                    if patch[key] is not None:
                        out.append(_line(key, patch[key]))
                else:
                    out.append(lines[i])
                i += 1
            for key, val in patch.items():
                if key not in seen and val is not None:
                    out.append(_line(key, val))
        path.write_text("".join(out), encoding="utf-8")
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
        for ln in path.read_text(encoding="utf-8").splitlines(keepends=True):
            if not dropping and ln.rstrip().startswith(f"  {repo_id}:") and not ln.startswith("   "):
                dropping = True
                continue
            if dropping:
                if ln.startswith("    "):
                    continue
                dropping = False            # anything shallower ends the block
            out.append(ln)
        path.write_text("".join(out), encoding="utf-8")
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
            # An unestablished repo's general dev turn runs as feature='onboarding', and that is
            # a workflow agent like any other.
            onboarding_running = c.execute(
                "SELECT COUNT(*) FROM run WHERE repo_id=? AND mode=? AND item_id IS NULL "
                "AND status='running' AND feature='onboarding'",
                (repo_id, mode)).fetchone()[0]
        return {"items_running": items_running, "learn_running": learn_running,
                "onboarding_running": onboarding_running}

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
            "learning_enabled": self.get_learning_enabled(),
            "deputy_enabled": self.get_deputy_enabled(),
            "deputy_strictness": self.deputy_strictness_map(),
            # "Unset" and "unset, which means Sonnet" are different answers: the picker needs
            # the first, the caption the second.
            "deputy_model": self.get_deputy_model(),
            "deputy_effort": self.get_deputy_effort(),
            "deputy_effective_model": self.deputy_params()[0],
            "deputy_effective_effort": self.deputy_params()[1],
            "live_runs": live,
            "running": len(live),
        }
