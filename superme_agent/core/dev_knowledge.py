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

# Display order for the work-item lifecycle (D-018). phase: triage → plan_design → build_eval → done.
# status (active): queued · in_progress · waiting · dropped — completion is a phase, not a status.
# `triage` = the pre-plan intake/classification phase (reserved; behaviour lands with the workspace
# workflow). Ranked first so intake items sort ahead of planned work.
_PHASE_RANK = {"triage": 0, "plan_design": 1, "build_eval": 2, "done": 3}
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


# --- general/ anchor docs: parse the readable deliverable/wave lists -------------
# The anchor docs carry NO frontmatter (see the dev-knowledge-structure constitution). Deliverables
# and waves live as clean id-tagged lists in the body: `- **<id>** — Title` under project-prd.md's
# `## Deliverables`, and `**<id>** — Title` under each `## <deliverable-id> …` heading in roadmap.md.
# A wave line may carry a curated status glyph (✓ done · ▸ active · · planned). These parsers are
# deliberately forgiving; example snippets inside ``` fences are skipped so they don't parse as data.
ANCHOR_DOCS = ("project-prd", "spec", "roadmap", "architecture")  # + resources/index.md
_DELIVERABLE_RE = re.compile(r"^-\s+\*\*(?P<id>[^*]+?)\*\*\s*[—–-]\s*(?P<title>.+?)\s*$", re.M)
_WAVE_RE = re.compile(r"\*\*(?P<id>[^*]+?)\*\*\s*[—–-]\s*(?P<title>.+?)\s*$")
_HEADING_RE = re.compile(r"^#{2,6}\s+(?P<id>\S+)")
_GLYPHS = {"✓": "done", "▸": "active", "·": "planned"}


def _strip_fences(text: str) -> str:
    """Drop ``` fenced code blocks so example snippets don't parse as real entries."""
    out, fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            out.append(line)
    return "\n".join(out)


def _section(text: str, heading: str) -> str:
    """Body under a `## <heading>` (case-insensitive), up to the next heading."""
    out, grab = [], False
    for line in text.splitlines():
        h = re.match(r"^#{1,6}\s+(.*)$", line)
        if h:
            grab = h.group(1).strip().lower() == heading.lower()
            continue
        if grab:
            out.append(line)
    return "\n".join(out)


def _deemph(s: str) -> str:
    """Strip markdown emphasis (`**`, `*`, `_`, backticks) so a title renders as plain text."""
    return re.sub(r"[*_`]", "", s).strip()


def _parse_deliverables(prd_text: str) -> list[dict]:
    """project-prd.md `## Deliverables` → [{id, title}] (in document order)."""
    body = _section(_strip_fences(prd_text or ""), "Deliverables")
    return [{"id": _deemph(m.group("id")), "title": _deemph(m.group("title"))}
            for m in _DELIVERABLE_RE.finditer(body)]


def _parse_waves(roadmap_text: str) -> list[dict]:
    """roadmap.md → [{id, title, deliverable, status}] — waves grouped under each `## <d-id> …`."""
    waves, current = [], None
    for line in _strip_fences(roadmap_text or "").splitlines():
        h = _HEADING_RE.match(line)
        if h:
            current = h.group("id").strip()
            continue
        if current is None:
            continue
        m = _WAVE_RE.search(line)
        if not m:
            continue
        glyph = next((_GLYPHS[c] for c in line if c in _GLYPHS), None)
        waves.append({"id": _deemph(m.group("id")), "title": _deemph(m.group("title")),
                      "deliverable": current, "status": glyph})
    return waves


def _first_para(text: str, cap: int = 240) -> str | None:
    """First real paragraph of a doc (skip headings/blockquotes/blanks), collapsed + length-capped."""
    buf: list[str] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or s.startswith(">"):
            if buf:
                break
            continue
        buf.append(s)
    if not buf:
        return None
    para = " ".join(buf)
    return para if len(para) <= cap else para[:cap].rsplit(" ", 1)[0] + "…"


def _item_view(it: dict) -> dict:
    """The board's per-item projection: identity + phase/status + the one relevant date (as display
    strings — the board never does date math, and a stringified date keeps the wire shape simple)."""
    done_at, updated, created = it.get("done_at"), it.get("updated_at"), it.get("created_at")
    date_val = done_at or (updated if it.get("status") in ("in_progress", "waiting") else None) or created
    return {"id": it.get("id"), "title": it.get("title"), "phase": it.get("phase"),
            "status": it.get("status"),
            "done_at": str(done_at) if done_at else None,
            "date": str(date_val) if date_val else None}


def _rollup(views: list[dict]) -> dict:
    """{done, total} for a set of item views — done = completed (done_at set)."""
    return {"done": sum(1 for v in views if v.get("done_at")), "total": len(views)}


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
            # Per-phase token accumulation (Stage D), BOTH bases recorded: `phase_tokens` = 3-type
            # (input+cache_write+output — what the card shows for its current phase) and
            # `phase_tokens_4type` = full volume (3-type + cache_read) behind it.
            by_phase = dict(s.get("by_phase", {}))
            by_phase_cr = dict(s.get("by_phase_cr", {}))
            # Legacy runs (made before the phase column existed) carry no phase → the "unknown" bucket.
            # Attribute them to the item's CURRENT phase: an item rarely leaves its first phase, and every
            # NEW run is stamped, so this only ever re-homes legacy spend (and is exact for the common
            # never-advanced case — e.g. an item that's lived entirely in plan_design shows its full total).
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
        chat), stored as its TIER ALIAS (`sonnet`) — the canonical on-disk form; the concrete latest
        is resolved at consumption (the run normalizes), so the pick auto-tracks a MODEL_TIERS bump.
        Reconfigurable anytime. Inserts the field if absent (older items predate it). Line-based
        rewrite preserving frontmatter shape, bumping `updated_at`. Returns True if the file changed."""
        from .models import model_family
        model = model_family(model) or model
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

    def set_work_item_scaffold(
        self, dev_root: Path, item_id: str, *, wave: str | None = None, deliverable: str | None = None
    ) -> bool:
        """Set a ROOT work-item's anchor-scaffold pointer — `wave: <id>` (which resolves its
        deliverable) or `deliverable: <id>` directly. Pass one; the other is cleared to null.
        Inserts the fields (next to parent_id) if absent. Bumps `updated_at`. Returns True if changed."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _meta, body = _parse_md(text)
        fm = m.group(1)

        def _set(fm: str, key: str, val: str) -> str:
            if re.search(rf"(?m)^{key}:", fm):
                return re.sub(rf"(?m)^{key}:.*$", f"{key}: {val}", fm)
            if re.search(r"(?m)^parent_id:", fm):
                return re.sub(r"(?m)^(parent_id:.*)$", rf"\1\n{key}: {val}", fm)
            return fm.rstrip() + f"\n{key}: {val}"

        fm = _set(fm, "wave", wave or "null")
        fm = _set(fm, "deliverable", deliverable or "null")
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    # --- general/ anchor docs ---------------------------------------------------

    def _general_path(self, dev_root: Path, name: str) -> Path | None:
        """Resolve an anchor-doc name to its path (guards the name — no traversal)."""
        if name == "resources":
            return Path(dev_root) / "general" / "resources" / "index.md"
        if name in ANCHOR_DOCS:
            return Path(dev_root) / "general" / f"{name}.md"
        return None

    def general_docs(self, dev_root: Path) -> list[dict]:
        """The anchor-doc set with presence flags (for the dashboard's Knowledge surface)."""
        out = []
        for name in (*ANCHOR_DOCS, "resources"):
            p = self._general_path(dev_root, name)
            out.append({"name": name, "present": bool(p and p.exists())})
        return out

    def read_general_doc(self, dev_root: Path, name: str) -> str | None:
        """Raw text of one anchor doc, or None (unknown name or missing file)."""
        p = self._general_path(dev_root, name)
        return p.read_text() if (p and p.exists()) else None

    def project_established(self, dev_root: Path) -> bool:
        """True once this project's memory exists — the PRD defines at least one deliverable (the
        spine the roadmap and work-items point at). A blank/missing general/ is 'not established':
        the dev workspace routes such a repo to onboarding (project-init / retrofit) as a hard front
        door before any work surfaces open. Keying on deliverables (not file presence) means a fresh
        repo — whose general/ doesn't exist yet — reads as un-established until onboarding fills it."""
        prd = self.read_general_doc(dev_root, "project-prd")
        return bool(prd and _parse_deliverables(prd))

    def write_general_doc(self, dev_root: Path, name: str, text: str) -> bool:
        """Overwrite one anchor doc (creating its folder if needed). False on an unknown name."""
        p = self._general_path(dev_root, name)
        if not p:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return True

    def roadmap_board(self, dev_root: Path, items: list[dict] | None = None) -> dict:
        """Join the anchor scaffold (project-prd deliverables + roadmap waves) with the live
        work-items (grouped by each item's own `wave:`/`deliverable:` pointer) into the board tree:
        deliverable → wave → its item views + rollup. Status/date come from the items; the wave's
        curated glyph comes from the roadmap. `orphans` surfaces referential-integrity breaks
        (a pointer to an id the anchor docs don't define)."""
        root = Path(dev_root)
        deliverables = _parse_deliverables(self.read_general_doc(dev_root, "project-prd") or "")
        waves = _parse_waves(self.read_general_doc(dev_root, "roadmap") or "")
        if items is None:
            items = self._read_work_items(root / "work-items")

        by_wave: dict[str, list] = {}
        by_deliv: dict[str, list] = {}
        for it in items:
            if it.get("wave"):
                by_wave.setdefault(str(it["wave"]), []).append(_item_view(it))
            elif it.get("deliverable"):
                by_deliv.setdefault(str(it["deliverable"]), []).append(_item_view(it))
            # items with neither pointer are simply off the board

        deliv_ids = {d["id"] for d in deliverables}
        wave_ids = {w["id"] for w in waves}
        dmap = {d["id"]: {**d, "waves": [], "items": []} for d in deliverables}
        orphans: list[dict] = []

        for w in waves:
            node = {**w, "items": by_wave.get(w["id"], []), "rollup": _rollup(by_wave.get(w["id"], []))}
            if w["deliverable"] in dmap:
                dmap[w["deliverable"]]["waves"].append(node)
            else:  # a wave under a deliverable the PRD doesn't define
                orphans.append({"reason": "wave-deliverable", "wave": w["id"], "deliverable": w["deliverable"]})
        for d_id, views in by_deliv.items():
            if d_id in dmap:
                dmap[d_id]["items"] = views
            else:
                orphans.append({"reason": "item-deliverable", "deliverable": d_id, "items": [v["id"] for v in views]})
        for w_id, views in by_wave.items():
            if w_id not in wave_ids:
                orphans.append({"reason": "item-wave", "wave": w_id, "items": [v["id"] for v in views]})

        # deliverable rollup = its waves' items + its direct items
        result = []
        for d in dmap.values():
            all_views = [v for wv in d["waves"] for v in wv["items"]] + d["items"]
            result.append({**d, "rollup": _rollup(all_views)})
        return {"deliverables": result, "orphans": orphans}

    def orient_digest(self, dev_root: Path, items: list[dict] | None = None) -> str | None:
        """The thin always-on ORIENT line for the dev preamble — what this project is, which waves are
        active, and what's in progress — generated from the anchor docs + live work-items. Kept tiny
        (it's a permanent per-turn cost); None when there's nothing yet (no general/ docs)."""
        prd = self.read_general_doc(dev_root, "project-prd")
        line = _first_para(prd) if prd else None
        board = self.roadmap_board(dev_root, items)
        active = [(w["title"], w["deliverable"]) for d in board["deliverables"]
                  for w in d["waves"] if w.get("status") == "active"]
        if items is None:
            items = self._read_work_items(Path(dev_root) / "work-items")
        inprog = [it for it in items
                  if it.get("status") in ("in_progress", "waiting") and not it.get("done_at")]
        if not (line or active or inprog):
            # Cold start: no project memory yet. Make the empty state VISIBLE (not silence) so the
            # charter's "Before there is any memory" rule fires — establish it before building.
            return ("**No project memory yet.** Establishing it is your first task — project-init for a "
                    "new/empty repo, retrofit for existing code (see the charter). Don't build until it exists.")
        out: list[str] = []
        if line:
            out.append(line)
        if active:
            out.append("Active: " + " · ".join(f"{t} ({d})" for t, d in active))
        if inprog:
            out.append("In progress: " + " · ".join(
                f"{it.get('title') or it.get('id')} [{it.get('phase')}]" for it in inprog[:6]))
        return "\n".join(out)

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
