"""Reading the dev tree: the item list, one item, its tasks and its artifacts."""

import re
from pathlib import Path

from .common import _iso_epoch, parse_md
from .parse import _PHASE_RANK, _STATUS_RANK, _norm_artifacts, _session_fields
from .general import _section


class ReadOps:
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
        """Attach run telemetry to each work-item, in place. The daemon passes run data
        in as plain dicts."""
        for it in items:
            wid = it.get("id")
            s = stats.get(wid, {})
            it["total_tokens"] = s.get("total_tokens", 0)
            # Both bases recorded: `phase_tokens` is the 3-type figure the card shows, `_4type` the full
            # volume behind it.
            by_phase = dict(s.get("by_phase", {}))
            by_phase_cr = dict(s.get("by_phase_cr", {}))
            # Legacy runs carry no phase. Attribute them to the item's CURRENT one — exact for a
            # never-advanced item.
            cur = it.get("phase")
            if cur:
                u = by_phase.pop("unknown", 0)
                if u:
                    by_phase[cur] = by_phase.get(cur, 0) + u
                ucr = by_phase_cr.pop("unknown", 0)
                if ucr:
                    by_phase_cr[cur] = by_phase_cr.get(cur, 0) + ucr
            it["phase_tokens"] = by_phase
            it["phase_tokens_4type"] = {p: by_phase.get(p, 0) + by_phase_cr.get(p, 0) for p in by_phase}
            it["last_run"] = (
                {"tokens": s.get("last_tokens", 0), "duration_ms": s.get("last_duration_ms"),
                 "model": s.get("last_model"), "ctx_pct": s.get("last_ctx_pct"),
                 # Epoch seconds: the surface renders the elapsed itself, so it keeps counting
                 # between polls.
                 "ended_at": _iso_epoch(s.get("last_ended_at"))}
                if s.get("runs") else None
            )
            info = live_by_item.get(wid)
            it["running"] = bool(info)
            it["run_started_at"] = _iso_epoch(info["started_at"]) if info else None
            it["run_tokens"] = info["tokens"] if info else None
            it["run_model"] = info.get("model") if info else None
            it["run_ctx_pct"] = info.get("ctx_pct") if info else None
            # The live run's role, so chat can label the indicator "Building…" rather than a
            # generic spinner.
            it["run_feature"] = info.get("feature") if info else None
            # Live run's, else the item's CONFIGURED model, else the last run's. A reconfigured
            # item shows what it WILL use.
            configured = it.get("model")
            it["model"] = (info.get("model") if info else None) or configured or s.get("last_model")
            it["ctx_pct"] = info.get("ctx_pct") if info else s.get("last_ctx_pct")
            it["tasks"] = self.task_progress(dev_root, wid)

    def read_work_item(self, dev_root: Path, item_id: str) -> dict | None:
        """Parse one work-item's `item.md` into a dict, or None. A single-item read that
        skips walking the tree."""
        p = Path(dev_root) / "work-items" / item_id / "item.md"
        if not p.exists():
            return None
        meta, body = parse_md(p.read_text(encoding="utf-8"))
        it = dict(meta)
        it["id"] = str(meta.get("id") or item_id)
        # An all-decimal 12-hex id parses from YAML as an int. Coerce.
        for k in ("root_id", "parent_id", "superseded_by"):
            if meta.get(k) is not None:
                it[k] = str(meta[k])
        if isinstance(meta.get("after"), (list, tuple)):
            it["after"] = [str(a) for a in meta["after"] if a]
        it["autopilot"] = bool(meta.get("autopilot"))
        it["prompt_extraction"] = bool(meta.get("prompt_extraction"))
        it["cohort"] = str(meta["cohort"]) if meta.get("cohort") else None
        it["description"] = body.strip()
        it["sessions"], it["session_id"] = _session_fields(meta)
        it["artifacts"] = _norm_artifacts(meta.get("artifacts"))  # legacy str → {type,path}
        return it

    def _task_lines(self, dev_root: Path, item_id: str) -> list[str]:
        """The item's checklist lines from plan.md's `## Tasks` — the living plan IS the task
        tracker. Legacy `tasks.md` falls back."""
        adir = Path(dev_root) / "work-items" / item_id / "artifacts"
        plan = adir / "plan.md"
        if plan.exists():
            body = _section(plan.read_text(encoding="utf-8"), "Tasks")
            if body.strip():
                return body.splitlines()
        legacy = adir / "tasks.md"
        if legacy.exists():
            return legacy.read_text(encoding="utf-8").splitlines()
        return []

    def task_progress(self, dev_root: Path, item_id: str) -> dict | None:
        """Checklist state → {done, total}, None when there are no task lines. Derived,
        never stored."""
        done = total = 0
        for line in self._task_lines(dev_root, item_id):
            m = re.match(r"\s*[-*]\s+\[([ xX])\]", line)
            if m:
                total += 1
                if m.group(1) in ("x", "X"):
                    done += 1
        if total == 0:
            return None
        return {"done": done, "total": total}

    def read_tasks(self, dev_root: Path, item_id: str) -> list[dict] | None:
        """The checklist as an ordered `{text, done}` list, None when empty — the same lines
        `task_progress` counts."""
        out: list[dict] = []
        for line in self._task_lines(dev_root, item_id):
            m = re.match(r"\s*[-*]\s+\[([ xX])\]\s*(.*)", line)
            if m:
                out.append({"text": m.group(2).strip(), "done": m.group(1) in ("x", "X")})
        return out or None

    def read_artifact_text(self, dev_root: Path, item_id: str, name: str) -> str | None:
        """An artifact file's Markdown body, frontmatter stripped. `name` is a bare
        filename — no path traversal."""
        if "/" in name or "\\" in name or name.startswith("."):
            return None
        p = Path(dev_root) / "work-items" / item_id / "artifacts" / name
        if not p.exists():
            return None
        _meta, body = parse_md(p.read_text(encoding="utf-8"))
        return body.strip() or None

    def work_item_session_ids(self, item: dict) -> list[str]:
        """Every session id an item holds — for lifecycle paths that must sweep ALL
        its threads, not just the current phase's."""
        out: list[str] = []
        for sid in [*(item.get("sessions") or {}).values(), item.get("session_id")]:
            if sid and sid not in out:
                out.append(str(sid))
        return out


    def _read_work_items(self, base: Path) -> list[dict]:
        """Walk `work-items/`: each folder with an `item.md` is one. Folder nesting IS the
        tree, so edges cannot drift."""
        if not base.exists():
            return []
        out = []
        for item_md in sorted(base.rglob("item.md")):
            rel = item_md.parent.relative_to(base)
            if any(part.startswith((".", "_")) for part in rel.parts):
                continue
            meta, body = parse_md(item_md.read_text(encoding="utf-8"))
            it = dict(meta)
            it["id"] = str(meta.get("id") or item_md.parent.name)
            it["root_id"] = rel.parts[0]
            it["parent_id"] = rel.parts[-2] if len(rel.parts) >= 2 else None
            it["depth"] = len(rel.parts) - 1
            it["description"] = body.strip()
            it["status"] = meta.get("status")
            if isinstance(meta.get("after"), (list, tuple)):
                it["after"] = [str(a) for a in meta["after"] if a]
            it["autopilot"] = bool(meta.get("autopilot"))
            it["prompt_extraction"] = bool(meta.get("prompt_extraction"))
            it["cohort"] = str(meta["cohort"]) if meta.get("cohort") else None
            it["artifacts"] = _norm_artifacts(meta.get("artifacts"))  # legacy str → {type,path}
            it["sessions"], it["session_id"] = _session_fields(meta)
            it["folder"] = str(rel)
            out.append(it)
        return out

    def _glance(self, items: list[dict], inbox: list[dict]) -> dict:
        by_status: dict[str, int] = {}
        by_phase: dict[str, int] = {}
        active, awaiting_human = [], []
        for it in items:
            by_phase[str(it.get("phase", "?"))] = by_phase.get(str(it.get("phase", "?")), 0) + 1
            # Display bucket: completion (done_at) reads as "done"; else the live status.
            key = "done" if it.get("done_at") else (str(it["status"]) if it.get("status") else "—")
            by_status[key] = by_status.get(key, 0) + 1
            if it.get("status") == "active":
                active.append({"id": it.get("id"), "title": it.get("title")})
            # The attention list: awaiting_human is the only status that pages the owner.
            if it.get("status") == "awaiting_human":
                awaiting_human.append({"id": it.get("id"), "title": it.get("title")})
        return {
            "by_status": by_status,
            "by_phase": by_phase,
            # SHIPPED ≠ TERMINAL: `done` counts everything that ended, abandoned work included.
            # Outcome is the discriminator.
            "shipped": sum(1 for it in items
                           if it.get("done_at")
                           and str(it.get("outcome") or "completed") == "completed"),
            "active": active,
            "awaiting_human": awaiting_human,
            "inbox_open": sum(1 for e in inbox if e.get("status", "open") == "open"),
            "counts": {"work_items": len(items)},
        }
