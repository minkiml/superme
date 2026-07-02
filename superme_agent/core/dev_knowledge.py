"""DevKnowledgeService — read the durable, file-based part of a context's `dev/` subtree.

Dev knowledge is a reserved, canonically-structured category of a Context's knowledge
(see the internal/dev README + D-018 — the contract). This service is the dedicated BE
consumer of the *file* structure: it walks `work-items/` (the v2 model — each folder is a
work-item, nesting is the branch-off tree) and parses the markdown + YAML frontmatter into
the item tree the dashboard renders, computing the derived view (blocked, root/children,
"glance") — never stored.

The inbox is no longer a file — it's an operational triage queue in DevStore (D-013/
D-014). The caller passes the current inbox rows into `read_all` so the glance and the
dashboard see one combined picture.

Surface-agnostic: it operates on a `dev_root` Path and never knows who's calling.
"""

import re
import json
import shutil
from datetime import date, datetime
from pathlib import Path

import yaml


def _iso_epoch(iso: str | None) -> float | None:
    """Spine ISO start stamp → epoch seconds, for the card's live elapsed-time timer."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _slug(s: str) -> str | None:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-") or None

# Display order for the work-item lifecycle (D-018). phase: plan_design → build_eval → done.
# status (active): queued · in_progress · waiting · dropped — completion is a phase, not a status.
_PHASE_RANK = {"plan_design": 0, "build_eval": 1, "done": 2}
_STATUS_RANK = {"in_progress": 0, "waiting": 1, "queued": 2, "dropped": 3}


def _norm_artifact(a) -> dict:
    """Normalize one `artifacts` frontmatter entry to the single `{type, path}` shape (R5).

    Agents have historically written EITHER a bare path string ("artifacts/plan.md") OR a structured
    `{type, path}` dict; this collapses both at the read boundary so the wire contract is one object
    union, never a string-or-object mix. `type` defaults to the filename stem (plan.md → "plan")."""
    def _stem(p: str) -> str:
        name = str(p or "").rsplit("/", 1)[-1]
        return name.rsplit(".", 1)[0] or "file"

    if isinstance(a, dict):
        path = str(a.get("path") or a.get("type") or "")
        kind = str(a.get("type") or _stem(path))
        return {"type": kind or "file", "path": path}
    s = str(a or "")
    return {"type": _stem(s), "path": s}


def _norm_artifacts(raw) -> list[dict]:
    """Coerce a raw `artifacts` frontmatter value (list, or None) to a list of normalized refs."""
    return [_norm_artifact(a) for a in (raw or [])]


def _parse_md(text: str) -> tuple[dict, str]:
    """Split a doc into (frontmatter dict, body). Tolerant: no/!invalid frontmatter -> {}."""
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, m.group(2)


class DevKnowledgeService:
    """Parse + lightly mutate the templated dev-knowledge structure under a dev_root."""

    def read_all(self, dev_root: Path, inbox: list[dict] | None = None) -> dict:
        root = Path(dev_root)
        inbox = inbox or []  # the triage queue comes from DevStore, not a file
        wi_dir = root / "work-items"
        items = self._read_work_items(wi_dir)

        by_id = {it["id"]: it for it in items}
        for it in items:
            it["children"] = []
        for it in items:
            pid = it.get("parent_id")
            if pid and pid in by_id:
                by_id[pid]["children"].append(it["id"])

        # `blocked` is derived (D-018): an active item is blocked when any `blocked_by`
        # dependency is unresolved (unknown id, or not completed/dropped). Never set by hand.
        def resolved(i: dict) -> bool:
            return bool(i.get("done_at")) or i.get("status") == "dropped"

        for it in items:
            blockers = it.get("blocked_by") or []
            it["blocked"] = (
                not resolved(it)
                and any((b not in by_id) or not resolved(by_id[b]) for b in blockers)
            )

        items.sort(key=lambda it: (
            _PHASE_RANK.get(str(it.get("phase")), 9),
            _STATUS_RANK.get(str(it.get("status")), 8),
            str(it.get("id", "")),
        ))

        return {
            "root": str(root),
            "exists": wi_dir.exists(),
            "work_items": items,
            "inbox": inbox,
            "glance": self._glance(items, inbox),
        }

    def enrich_work_items(self, dev_root: Path, items: list[dict],
                          live_by_item: dict[str, dict], stats: dict[str, dict]) -> None:
        """Attach run telemetry to each work-item, in place (R5 push-down, decision #7).

        The daemon owns the spine, so it passes the run data in as plain dicts — `live_by_item`
        (item_id → live running row) and `stats` (item_id → accumulated run stats) — keeping this
        service free of any spine dependency. Adds the live (time/tokens while running) + accumulated
        (total tokens, last run) + display (model, context_pct, tasks-progress) fields the cards show.
        """
        for it in items:
            wid = it.get("id")
            s = stats.get(wid, {})
            it["total_tokens"] = s.get("total_tokens", 0)
            it["last_run"] = (
                {"tokens": s.get("last_tokens", 0), "duration_ms": s.get("last_duration_ms"),
                 "model": s.get("last_model"), "context_pct": s.get("last_context_pct")}
                if s.get("runs") else None
            )
            info = live_by_item.get(wid)
            it["running"] = bool(info)
            it["run_started_at"] = _iso_epoch(info["started_at"]) if info else None
            it["run_tokens"] = info["tokens"] if info else None
            it["run_model"] = info.get("model") if info else None
            it["run_context_pct"] = info.get("ctx_pct") if info else None
            # The model to show: the live run's, else the item's CONFIGURED model (frontmatter — what
            # its runs use), else the last run's. Configured wins over telemetry so a reconfigured-
            # but-not-yet-rerun item shows what it WILL use.
            configured = it.get("model")
            it["model"] = (info.get("model") if info else None) or configured or s.get("last_model")
            it["context_pct"] = info.get("ctx_pct") if info else s.get("last_context_pct")
            it["tasks"] = self.task_progress(dev_root, wid)

    def create_work_item(
        self,
        dev_root: Path,
        title: str,
        description: str = "",
        *,
        session_id: str | None = None,
        source: str | None = None,
        inbox_id: int | None = None,
    ) -> dict:
        """Stamp a new top-level work-item from a pushed inbox item (D-018).

        Creates `work-items/<id>/{item.md, artifacts/}`, entering at phase=plan_design,
        status=queued. The id is a unique slug of the title (the folder name IS the id — the
        source of truth for the work-graph). Returns {id, folder}. The creation itself is
        recorded as an `inbox.push` event in the LOG (PRD §4.9) — no per-item log.md file.
        """
        wi = Path(dev_root) / "work-items"
        wi.mkdir(parents=True, exist_ok=True)
        title = (title or "").strip()
        base = _slug(title) or "item"
        wid, i = base, 2
        while (wi / wid).exists():
            wid, i = f"{base}-{i}", i + 1
        folder = wi / wid
        (folder / "artifacts").mkdir(parents=True, exist_ok=True)

        today = date.today().isoformat()
        fm = (
            f"---\nid: {wid}\nroot_id: {wid}\nparent_id: null\n"
            f"title: {json.dumps(title)}\nphase: plan_design\nstatus: queued\n"
            f"done_at: null\nartifacts: []\nblocked_by: []\n"
            f"session_id: {json.dumps(session_id) if session_id else 'null'}\n"
            f"created_at: {today}\nupdated_at: {today}\n---\n"
        )
        body = (description or "").strip()
        (folder / "item.md").write_text(fm + (body + "\n" if body else ""))
        return {"id": wid, "folder": wid}

    def read_work_item(self, dev_root: Path, item_id: str) -> dict | None:
        """Parse one work-item's `item.md` (frontmatter + body) into a dict, or None if it
        doesn't exist. Lightweight single-item read for callers that need just one item's
        frontmatter (e.g. its `session_id` to resume) without walking the whole tree."""
        p = Path(dev_root) / "work-items" / item_id / "item.md"
        if not p.exists():
            return None
        meta, body = _parse_md(p.read_text())
        it = dict(meta)
        it["id"] = str(meta.get("id") or item_id)
        it["description"] = body.strip()
        it["session_id"] = meta.get("session_id")
        it["artifacts"] = _norm_artifacts(meta.get("artifacts"))  # legacy str → {type,path} (R5)
        return it

    def task_progress(self, dev_root: Path, item_id: str) -> dict | None:
        """Count the checklist state in a work-item's `artifacts/tasks.md` → {done, total},
        or None if there's no tasks.md. Reads Markdown task lines (`- [ ]` / `- [x]`), the
        living to-do list the plan skill writes — lets the card show a build-progress glance."""
        p = Path(dev_root) / "work-items" / item_id / "artifacts" / "tasks.md"
        if not p.exists():
            return None
        done = total = 0
        for line in p.read_text().splitlines():
            m = re.match(r"\s*[-*]\s+\[([ xX])\]", line)
            if m:
                total += 1
                if m.group(1) in ("x", "X"):
                    done += 1
        if total == 0:
            return None
        return {"done": done, "total": total}

    def read_tasks(self, dev_root: Path, item_id: str) -> list[dict] | None:
        """Parse `artifacts/tasks.md` into an ordered list of `{text, done}` checklist items
        (None if there's no tasks.md or it has no task lines). The structured form the review
        popup renders — same `- [ ]` / `- [x]` lines `task_progress` counts."""
        p = Path(dev_root) / "work-items" / item_id / "artifacts" / "tasks.md"
        if not p.exists():
            return None
        out: list[dict] = []
        for line in p.read_text().splitlines():
            m = re.match(r"\s*[-*]\s+\[([ xX])\]\s*(.*)", line)
            if m:
                out.append({"text": m.group(2).strip(), "done": m.group(1) in ("x", "X")})
        return out or None

    def read_artifact_text(self, dev_root: Path, item_id: str, name: str) -> str | None:
        """Return an artifact file's Markdown body (frontmatter stripped), or None if absent.
        Used by the review popup to render plan.md / prd.md as structured content. `name` is a
        bare filename under the item's `artifacts/` — no path traversal."""
        if "/" in name or "\\" in name or name.startswith("."):
            return None
        p = Path(dev_root) / "work-items" / item_id / "artifacts" / name
        if not p.exists():
            return None
        _meta, body = _parse_md(p.read_text())
        return body.strip() or None

    def set_work_item_phase(self, dev_root: Path, item_id: str, phase: str) -> bool:
        """Set a work-item's `phase` (plan_design / build_eval / done) — the owner's gate,
        advanced on approval. Line-based rewrite preserving frontmatter shape, bumping
        `updated_at`. Returns True if the file changed."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get("phase")) == str(phase):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = re.sub(r"(?m)^phase:.*$", f"phase: {phase}", m.group(1))
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_done(self, dev_root: Path, item_id: str) -> bool:
        """Mark a work-item COMPLETE — stamp `done_at` (the tick-out off the Done phase). Sets
        the field to today (inserting it for older items that predate it) and bumps `updated_at`.
        Returns True if the file changed (False if already completed or missing)."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if meta.get("done_at"):
            return False  # already completed
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        today = date.today().isoformat()
        fm = m.group(1)
        if re.search(r"(?m)^done_at:.*$", fm):
            fm = re.sub(r"(?m)^done_at:.*$", f"done_at: {today}", fm)
        else:
            fm = fm.rstrip() + f"\ndone_at: {today}"
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {today}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def write_artifact(self, dev_root: Path, item_id: str, name: str, text: str) -> bool:
        """Write a file into a work-item's `artifacts/` (daemon-side, e.g. the execution archive).
        Creates the folder if needed. Returns True if the item folder exists."""
        folder = Path(dev_root) / "work-items" / item_id
        if not folder.is_dir():
            return False
        adir = folder / "artifacts"
        adir.mkdir(exist_ok=True)
        (adir / name).write_text(text)
        return True

    def set_work_item_model(self, dev_root: Path, item_id: str, model: str) -> bool:
        """Set a work-item's configured `model` (the agent model its runs use — plan + bound
        chat). Reconfigurable anytime. Inserts the field if absent (older items predate it).
        Line-based rewrite preserving frontmatter shape, bumping `updated_at`. Returns True if
        the file changed."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get("model")) == str(model):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = m.group(1)
        if re.search(r"(?m)^model:", fm):
            fm = re.sub(r"(?m)^model:.*$", f"model: {model}", fm)
        elif re.search(r"(?m)^status:", fm):
            fm = re.sub(r"(?m)^(status:.*)$", rf"\1\nmodel: {model}", fm)  # sits next to status
        else:
            fm = fm.rstrip() + f"\nmodel: {model}"
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_effort(self, dev_root: Path, item_id: str, effort: str) -> bool:
        """Set a work-item's configured reasoning `effort` (low|medium|high) — used by its runs
        (plan + bound chat). Mirrors `set_work_item_model`: inserts the field if absent, line-based
        rewrite preserving frontmatter shape, bumps `updated_at`. Returns True if the file changed."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get("effort")) == str(effort):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = m.group(1)
        if re.search(r"(?m)^effort:", fm):
            fm = re.sub(r"(?m)^effort:.*$", f"effort: {effort}", fm)
        elif re.search(r"(?m)^model:", fm):
            fm = re.sub(r"(?m)^(model:.*)$", rf"\1\neffort: {effort}", fm)  # sits next to model
        else:
            fm = fm.rstrip() + f"\neffort: {effort}"
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_session(self, dev_root: Path, item_id: str, session_id: str | None) -> bool:
        """Persist the agent `session_id` onto a work-item's frontmatter — the item is the
        durable home of its dev thread (resume from here next time). Line-based rewrite to
        preserve frontmatter shape; bumps `updated_at`. Returns True if the file changed."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get("session_id")) == str(session_id):
            return False  # unchanged — skip the write
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        today = date.today().isoformat()
        sid = json.dumps(session_id) if session_id else "null"
        fm = re.sub(r"(?m)^session_id:.*$", f"session_id: {sid}", m.group(1))
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {today}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_status(self, dev_root: Path, item_id: str, status: str) -> bool:
        """Set a work-item's `status` (the run-state axis — queued/in_progress/waiting/...),
        line-based rewrite preserving frontmatter shape, bumping `updated_at`. Status is
        ORCHESTRATOR-OWNED (the daemon sets it on run start/finish), not the agent's to set.
        Returns True if the file changed."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get("status")) == str(status):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = re.sub(r"(?m)^status:.*$", f"status: {status}", m.group(1))
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def delete_work_item(self, dev_root: Path, item_id: str) -> bool:
        """Hard-delete a work-item folder (item.md, artifacts/ and any branch-offs nested
        under it). Returns True if a folder was removed. Caller enforces any phase
        guard (only plan_design items are deletable — past that, code may be touched)."""
        folder = Path(dev_root) / "work-items" / item_id
        if not folder.is_dir():
            return False
        shutil.rmtree(folder)
        return True

    # NOTE: the legacy `add_decision` (D-###.md scheme) was retired — decisions are now a
    # `memory` type in the §4.9 knowledge subsystem, not an orphan folder. See PRD §4.9.

    # --- memory: RETIRED (WI-8) --------------------------------------------------
    # The curated dev/`memory/` applied-fact store (index MEMORY.md + one-fact files) is gone.
    # Learned operational content is now constitution/skill/agent in the harness, governed via the
    # Published inventory; auto-accrued knowledge lives in the knowledge tree. The reader/writer
    # methods (list_memory / read_memory_index / apply_fact / set_fact_enabled) were removed here.

    def read_model(self, dev_root: Path, fallback_root: Path | None = None) -> dict:
        """The canonical model manifest (model.yaml): the shared *shape* of dev-knowledge.

        The model is shared structure, so a context with no manifest of its own falls back
        to the global reference home's.
        """
        empty = {"exists": False, "entities": [], "edges": [], "lifecycle": {}, "flow": []}
        path = Path(dev_root) / "model.yaml"
        if not path.exists() and fallback_root is not None:
            path = Path(fallback_root) / "model.yaml"
        if not path.exists():
            return empty
        try:
            model = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            return empty
        if not isinstance(model, dict):
            return empty
        model["exists"] = True
        return model

    # --- internals --------------------------------------------------------------

    def _read_work_items(self, base: Path) -> list[dict]:
        """Walk `work-items/`: each folder containing an `item.md` is a work-item; folder
        nesting is the branch-off tree (parent_id/root_id derived from the path — the folder
        is the source of truth, so they can't drift). `item.md` body is the description.
        """
        if not base.exists():
            return []
        out = []
        for item_md in sorted(base.rglob("item.md")):
            rel = item_md.parent.relative_to(base)
            if any(part.startswith((".", "_")) for part in rel.parts):
                continue
            meta, body = _parse_md(item_md.read_text())
            it = dict(meta)
            it["id"] = str(meta.get("id") or item_md.parent.name)
            it["root_id"] = rel.parts[0]
            it["parent_id"] = rel.parts[-2] if len(rel.parts) >= 2 else None
            it["depth"] = len(rel.parts) - 1
            it["description"] = body.strip()
            it["status"] = meta.get("status")  # may be None (completed/unset)
            it["blocked_by"] = meta.get("blocked_by") or []
            it["artifacts"] = _norm_artifacts(meta.get("artifacts"))  # legacy str → {type,path} (R5)
            it["session_id"] = meta.get("session_id")  # origin session, may be None
            it["folder"] = str(rel)
            out.append(it)
        return out

    def _glance(self, items: list[dict], inbox: list[dict]) -> dict:
        by_status: dict[str, int] = {}
        by_phase: dict[str, int] = {}
        blocked, in_progress, waiting = [], [], []
        for it in items:
            by_phase[str(it.get("phase", "?"))] = by_phase.get(str(it.get("phase", "?")), 0) + 1
            # Display bucket: completion (done_at) reads as "done"; else the active status.
            key = "done" if it.get("done_at") else (str(it["status"]) if it.get("status") else "—")
            by_status[key] = by_status.get(key, 0) + 1
            if it.get("blocked"):
                blocked.append({"id": it.get("id"), "blocked_by": it.get("blocked_by") or []})
            if it.get("status") == "in_progress":
                in_progress.append({"id": it.get("id"), "title": it.get("title")})
            if it.get("status") == "waiting":
                waiting.append({"id": it.get("id"), "blocked_by": it.get("blocked_by") or []})
        return {
            "by_status": by_status,
            "by_phase": by_phase,
            "in_progress": in_progress,
            "waiting": waiting,
            "blocked": blocked,
            "inbox_open": sum(1 for e in inbox if e.get("status", "open") == "open"),
            "counts": {"work_items": len(items)},
        }
