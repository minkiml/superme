"""DevKnowledgeService — read the durable, file-based part of a context's `dev/` subtree.

Walks `work-items/`, parsing markdown plus YAML frontmatter into the item tree the dashboard
renders. The inbox is not a file: its rows are passed in.
"""

import re
import json
import shutil
import secrets
from datetime import date, datetime
from pathlib import Path

import yaml

from . import sandbox
from .titles import check_title, normalize_title


_KNOWLEDGE_IGNORE = """\
# A work-item's `scratch/` is a run's working space — inventories, sorted lists, a helper script.
# Nothing downstream reads it and nothing keeps it past the item, so none of it belongs in the
# knowledge history. One rule, at the root, covers every repo's sub-home.
*/dev/work-items/*/scratch/
"""


def _ensure_knowledge_ignore(dev_root: Path) -> None:
    """Write the knowledge home's `.gitignore` if it has none. At the ROOT,
    because the home may get its own remote."""
    try:
        root = Path(dev_root).parent.parent
        marker = root / ".gitignore"
        if root.is_dir() and not marker.exists():
            marker.write_text(_KNOWLEDGE_IGNORE)
    except OSError:
        pass


def _iso_epoch(iso: str | None) -> float | None:
    """Spine ISO start stamp → epoch seconds, for the card's live elapsed-time timer."""
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)


def _session_fields(meta: dict) -> tuple[dict, str | None]:
    """A work-item's session slots plus the COMPUTED `session_id` — the current
    phase's, so single-session readers follow the phase."""
    from .kind_profiles import (INTAKE_PHASES, LEGACY_INTAKE_SLOT, SESSION_SLOTS, session_slot)
    keys = (*SESSION_SLOTS, LEGACY_INTAKE_SLOT)
    sessions = {s: str(meta[f"session_{s}"]) for s in keys if meta.get(f"session_{s}")}
    phase = str(meta.get("phase") or "triage")
    try:
        slot = session_slot(phase)
    except KeyError:
        slot = "triage"
    legacy_intake = sessions.get(LEGACY_INTAKE_SLOT) if slot in INTAKE_PHASES else None
    legacy_id = meta.get("session_id")
    computed = (sessions.get(slot) or legacy_intake
                or (str(legacy_id) if legacy_id else None))
    return sessions, computed


# Display order. Status ranks put what NEEDS THE OWNER first, then runnable work, then waits, then
# done.
_PHASE_RANK = {"triage": 0, "plan": 1, "build": 2, "investigate": 2,
               "vet": 3, "review": 4, "close": 5}
# `error` outranks awaiting_human: work that STOPPED is louder than work resting at a gate by
# design.
_STATUS_RANK = {"error": 0, "awaiting_human": 1, "active": 2, "awaiting_child": 3,
                "awaiting_upstream": 4, "awaiting_slot": 4, "done": 5}
# Non-terminal. A parked item IS live, and so is `error` — it is work waiting to be resumed.
_LIVE_STATUSES = ("active", "awaiting_child", "awaiting_upstream", "awaiting_slot",
                  "awaiting_human", "error")
_SPAWN_RELATIONS = ("blocking", "parallel", "spawn")


def _toposort_keys(specs: list[dict]) -> list[str]:
    """Order batch keys so every intra-batch `after` comes before its dependent.
    Raises naming the cycle if not a DAG."""
    keys = {s["key"] for s in specs}
    deps = {s["key"]: [a for a in s["after"] if a in keys] for s in specs}
    order: list[str] = []
    temp: set[str] = set()
    perm: set[str] = set()

    def visit(k: str, stack: list[str]) -> None:
        if k in perm:
            return
        if k in temp:
            raise ValueError(f"cyclic after: edge — {' → '.join(stack + [k])}")
        temp.add(k)
        for d in deps[k]:
            visit(d, stack + [k])
        temp.discard(k)
        perm.add(k)
        order.append(k)

    for s in specs:
        visit(s["key"], [])
    return order


def _norm_artifact(a) -> dict:
    """Normalize one `artifacts` entry to `{type, path}`. Agents write either a bare
    path or the dict."""
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


# One owner per fact: PRD what, architecture how, capabilities now, decisions why, roadmap next,
# resources where.
ANCHOR_DOCS = ("project-prd", "architecture", "capabilities",
               "decisions", "roadmap", "verification")
# An anchor doc so the owner edits it where they read the others, but exempt from the lint.
_LIBRARY_DOC = "verification"
LEGACY_DOCS = ("spec",)
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


# Anchor docs are prose, not a form, so every reader degrades to empty. An unanswered band does
# not render.
_KV_RE = re.compile(r"^\s*-\s+\*\*(?P<k>[^*]+?)\*\*\s*[:—–-]\s*(?P<v>.+?)\s*$")
# Colon-only, to tell STRUCTURE from a sentence that merely opens in bold.
_KV_STRICT_RE = re.compile(r"^\s*-\s+\*\*[^*]+?\*\*\s*:\s*.+$")
_PLAIN_BULLET_RE = re.compile(r"^\s*-\s+(?P<v>.+?)\s*$")


def _kv_bullets(section: str) -> dict[str, str]:
    """`- **Key**: value` bullets → {lowercased key: value}. Callers match case-insensitively."""
    return {_deemph(m.group("k")).lower(): _deemph(m.group("v"))
            for m in (_KV_RE.match(ln) for ln in section.splitlines()) if m}


def _kv_list(section: str) -> list[dict]:
    """The same shape, ORDER-PRESERVING and repeat-tolerant — for stack rows, where duplicate
    keys are legitimate."""
    return [{"key": _deemph(m.group("k")), "value": _deemph(m.group("v"))}
            for m in (_KV_RE.match(ln) for ln in section.splitlines()) if m]


def _bullets(section: str) -> list[str]:
    """`- text` bullets as sentences. A `**Bold lead** —` is kept; a true `- **Key**: value`
    row belongs to `_kv_*`."""
    return [_deemph(m.group("v"))
            for ln in section.splitlines()
            if (m := _PLAIN_BULLET_RE.match(ln)) and not _KV_STRICT_RE.match(ln)]


def _project_name(prd_text: str) -> str | None:
    """The project's own name from the PRD's `# <name> — …` H1, minus any trailing doc label."""
    m = re.search(r"^#\s+(.+?)\s*$", prd_text or "", re.M)
    return re.split(r"\s+[—–-]\s+", _deemph(m.group(1)))[0] if m else None


# Kept as sub-bullets, not folded into the title line, so every existing PRD keeps parsing.
_D_FIELD_RE = re.compile(r"^\s+-\s+\*\*(?P<key>Value|Needs)\*\*\s*:\s*(?P<val>.+?)\s*$")


def _parse_deliverables(prd_text: str) -> list[dict]:
    """project-prd.md `## Deliverables` → [{id, title, value, needs}]. The
    sub-fields are additive, so a v1 PRD still parses."""
    body = _section(_strip_fences(prd_text or ""), "Deliverables")
    out: list[dict] = []
    for line in body.splitlines():
        m = _DELIVERABLE_RE.match(line)
        if m:
            out.append({"id": _deemph(m.group("id")), "title": _deemph(m.group("title")),
                        "value": None, "needs": []})
            continue
        f = _D_FIELD_RE.match(line)
        if f and out:                      # a sub-field belongs to the deliverable above it
            if f.group("key") == "Value":
                out[-1]["value"] = _deemph(f.group("val"))
            else:
                out[-1]["needs"] = [s for s in
                                    (x.strip() for x in _deemph(f.group("val")).split(",")) if s]
    return out


# An open question is an ADDRESSABLE record, not prose. The id is what lets the owner answer it.
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
    """The board's per-item projection: identity, phase/status, and the one relevant date
    as a display string."""
    done_at, updated, created = it.get("done_at"), it.get("updated_at"), it.get("created_at")
    date_val = done_at or (updated if it.get("status") in _LIVE_STATUSES else None) or created
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

    def create_work_item(
        self,
        dev_root: Path,
        title: str,
        description: str = "",
        *,
        session_id: str | None = None,
        kind: str,
        proposed_kind: str | None = None,
        spawned_from: dict | None = None,
        inbox_id: int | None = None,
        after: list[str] | None = None,
        autopilot: bool = False,
        cohort: str | None = None,
        prompt_extraction: bool = False,
        research_kind: str | None = None,
        born_at: str | None = None,
    ) -> dict:
        """Stamp a new top-level work-item from a pushed inbox row, entering at
        triage/active.

        `kind` is REQUIRED with no default — a default gave every ticket an implementation pipeline.
        `proposed_kind` is birth provenance, never routed on. Peers are validated to EXIST."""
        from .kind_profiles import DEFAULT_SCALE, KIND_PROFILES, RESEARCH_KINDS, get_profile
        profile = get_profile(kind)
        # A button-launched sweep is born classified: the button IS the classification. Every
        # other mint point enters at phase 0.
        if proposed_kind is not None and proposed_kind not in KIND_PROFILES:
            raise ValueError(f"proposed_kind must be one of {sorted(KIND_PROFILES)}")
        if research_kind is not None:
            if kind != "research":
                raise ValueError("research_kind belongs only to a research item")
            if research_kind not in RESEARCH_KINDS:
                raise ValueError(f"research_kind must be one of {RESEARCH_KINDS}")
        if born_at is not None and born_at not in profile.phases:
            raise ValueError(f"a {profile.kind} item has no `{born_at}` phase "
                             f"(its phases are {profile.phases})")
        if spawned_from is not None:
            if not isinstance(spawned_from, dict) or not spawned_from.get("item"):
                raise ValueError("spawned_from must be {item, relation[, note]}")
            if spawned_from.get("relation") not in _SPAWN_RELATIONS:
                raise ValueError(f"spawned_from.relation must be one of {_SPAWN_RELATIONS}")
        wi = Path(dev_root) / "work-items"
        wi.mkdir(parents=True, exist_ok=True)
        # Validated against disk BEFORE any write: a mistyped upstream id would park this item
        # forever.
        after_ids = [str(a) for a in (after or []) if a]
        missing = [a for a in after_ids if not (wi / a / "item.md").exists()]
        if missing:
            raise ValueError(f"after references unknown work-item(s): {', '.join(missing)}")
        # The FLOOR under every mint point. Agents get bounced at their own tool instead, so they
        # learn.
        title = normalize_title(title, description=description)
        wid = secrets.token_hex(6)                       # opaque 12-hex id == folder name (~2^48)
        while (wi / wid).exists():
            wid = secrets.token_hex(6)
        folder = wi / wid
        # BOTH homes exist from birth: an item's own folders are the kernel's to make, not an
        # agent's.
        for sub in ("artifacts", "reports"):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        _ensure_knowledge_ignore(dev_root)

        today = date.today().isoformat()
        # Optional provenance lines — written only when set (absent = null on read; no dead fields).
        extra = ""
        if spawned_from is not None:
            edge = {"item": str(spawned_from["item"]), "relation": spawned_from["relation"]}
            if spawned_from.get("note"):
                edge["note"] = str(spawned_from["note"])
            extra += f"spawned_from: {json.dumps(edge)}\n"   # JSON is valid YAML flow mapping
        if inbox_id is not None:
            extra += f"inbox_id: {inbox_id}\n"
        # What the FILER proposed. Frozen at birth, read by exactly one reader — triage. Absent
        # means nobody judged.
        if proposed_kind:
            extra += f"proposed_kind: {proposed_kind}\n"
        # A per-item POLICY: does the workflow drive itself through this item's gates. Written
        # only when on.
        if autopilot:
            extra += "autopilot: true\n"
        # A disposable item minted to run a real lifecycle so its per-phase prompts get captured,
        # then torn down.
        if prompt_extraction:
            extra += "prompt_extraction: true\n"
        # The batch this item was itemized with. Written only when set — the manual majority
        # belongs to no cohort.
        if cohort:
            extra += f"cohort: {json.dumps(str(cohort))}\n"
        status = "active"
        if after_ids:
            extra += f"after: {json.dumps(after_ids)}\n"
            # Park immediately: an item must never spend even one scheduler tick `active` against
            # work that hasn't landed.
            unfinished = [a for a in after_ids
                          if str((self.read_work_item(dev_root, a) or {}).get("status")) != "done"]
            if unfinished:
                status = "awaiting_upstream"
        fm = (
            f"---\nid: {wid}\nroot_id: {wid}\nparent_id: null\n"
            f"title: {json.dumps(title)}\nkind: {profile.kind}\n"
            # Born `standard`. Triage judges it, with a reason, before the item leaves the first
            # phase.
            f"scale: {DEFAULT_SCALE}\nscale_reason: null\n"
            # Born unjudged — there is no default family. Triage names one; a button-launched
            # sweep already has it.
            f"research_kind: {json.dumps(research_kind) if research_kind else 'null'}\n"
            f"research_kind_reason: "
            f"{json.dumps('launched from the ' + research_kind + ' button') if research_kind else 'null'}\n"
            f"phase: {born_at or profile.phases[0]}\nstatus: {status}\n"
            f"done_at: null\nartifacts: []\n{extra}"
            f"session_id: {json.dumps(session_id) if session_id else 'null'}\n"
            f"created_at: {today}\nupdated_at: {today}\n---\n"
        )
        body = (description or "").strip()
        (folder / "item.md").write_text(fm + (body + "\n" if body else ""))
        return {"id": wid, "folder": wid}

    def itemize_launch(self, dev_root: Path, items: list[dict], *,
                       session_id: str | None = None, cohort: str | None = None) -> dict:
        """Create a LAUNCH COHORT: every item born `autopilot=true` under one shared
        `cohort` id. `key` is a caller-local handle for intra-batch edges."""
        if not items:
            raise ValueError("itemize_launch needs at least one item")
        specs: list[dict] = []
        seen: set[str] = set()
        for i, it in enumerate(items):
            key = str(it.get("key") or "").strip()
            title = str(it.get("title") or "").strip()
            if not key:
                raise ValueError(f"item #{i} is missing a `key` (its batch-local edge handle)")
            # Itemization names a cohort in one act, and the caller is an agent, so a complaint
            # lands as a retry.
            if (bad := check_title(title, description=str(it.get("description") or ""))):
                raise ValueError(f"item {key!r}: {bad}")
            if key in seen:
                raise ValueError(f"duplicate item key {key!r}")
            seen.add(key)
            # No default kind: an itemizer usually KNOWS which it is, so it is asked.
            item_kind = str(it.get("kind") or "").strip()
            if not item_kind:
                raise ValueError(f"item {key!r} is missing a `kind` (implementation | research)")
            specs.append({"key": key, "title": title,
                          "description": str(it.get("description") or ""),
                          "kind": item_kind,
                          "after": [str(a) for a in (it.get("after") or []) if a]})
        keys = {s["key"] for s in specs}
        for s in specs:
            for a in s["after"]:
                if a in keys or (Path(dev_root) / "work-items" / a / "item.md").exists():
                    continue
                raise ValueError(f"item {s['key']!r} lists after={a!r}, which is neither another "
                                 f"item in this batch nor an existing work-item")
        order = _toposort_keys(specs)
        cohort_id = str(cohort) if cohort else secrets.token_hex(4)
        key_to_id: dict[str, str] = {}
        created: list[dict] = []
        for key in order:
            s = next(x for x in specs if x["key"] == key)
            after_ids = [key_to_id.get(a, a) for a in s["after"]]
            # The caller's kind is both what it runs as and what it proposed — these skip the
            # inbox, not triage.
            wi = self.create_work_item(dev_root, s["title"], s["description"],
                                       session_id=session_id, kind=s["kind"],
                                       proposed_kind=s["kind"],
                                       after=after_ids, autopilot=True, cohort=cohort_id)
            key_to_id[key] = wi["id"]
            item = self.read_work_item(dev_root, wi["id"]) or {}
            created.append({"id": wi["id"], "key": key, "title": s["title"],
                            "after": after_ids, "status": str(item.get("status") or "active")})
        # Preserve the caller's input order in the return (topo order is an implementation detail).
        created.sort(key=lambda c: [s["key"] for s in specs].index(c["key"]))
        return {"cohort": cohort_id, "created": created,
                "running": [c["id"] for c in created if c["status"] == "active"],
                "waiting": [c["id"] for c in created if c["status"] == "awaiting_upstream"]}

    def read_work_item(self, dev_root: Path, item_id: str) -> dict | None:
        """Parse one work-item's `item.md` into a dict, or None. A single-item read that
        skips walking the tree."""
        p = Path(dev_root) / "work-items" / item_id / "item.md"
        if not p.exists():
            return None
        meta, body = _parse_md(p.read_text())
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
            body = _section(plan.read_text(), "Tasks")
            if body.strip():
                return body.splitlines()
        legacy = adir / "tasks.md"
        if legacy.exists():
            return legacy.read_text().splitlines()
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
        _meta, body = _parse_md(p.read_text())
        return body.strip() or None

    def set_work_item_phase(self, dev_root: Path, item_id: str, phase: str) -> bool:
        """Set a work-item's `phase`. Sequencing validity is the caller's job via
        `kind_profiles.next_phase`."""
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

    def set_work_item_title(self, dev_root: Path, item_id: str, title: str) -> bool:
        """Rename a work-item — triage's naming act. Safe by construction: the folder
        name is the id, so nothing keys on the title.

        Raises ValueError on a title that fails `check_title`, so a bad rename never lands."""
        if (bad := check_title(title)):
            raise ValueError(bad)
        title = normalize_title(title)
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get("title") or "") == title:
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        # LAMBDA, not an f-string: `re.sub` parses backslashes in a replacement string, and
        # `json.dumps` emits `\uXXXX`.
        fm = re.sub(r"(?m)^title:.*$", lambda _m: f"title: {json.dumps(title)}", m.group(1))
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_kind(self, dev_root: Path, item_id: str, kind: str) -> bool:
        """Record a work-item's `kind` — triage's surface. Validated against
        KIND_PROFILES, loud on unknown."""
        from .kind_profiles import get_profile
        get_profile(kind)
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get("kind")) == str(kind):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = re.sub(r"(?m)^kind:.*$", f"kind: {kind}", m.group(1))
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_scale(self, dev_root: Path, item_id: str, scale: str,
                            reason: str) -> bool:
        """Set `scale` plus the one-line `scale_reason`. The reason is REQUIRED even
        for `standard` — a bare label is unarguable at the gate."""
        from .kind_profiles import ITEM_SCALES
        if scale not in ITEM_SCALES:
            raise ValueError(f"scale must be one of {'/'.join(ITEM_SCALES)} (got {scale!r})")
        if not (reason or "").strip():
            raise ValueError("scale needs a one-line reason — it is what the owner argues with")
        one_line = " ".join(str(reason).split())
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _, body = _parse_md(text)
        fm = m.group(1)
        # Every replacement is a LAMBDA: `re.sub` parses backslash escapes in a replacement
        # string.
        pair = f"scale: {scale}\nscale_reason: {json.dumps(one_line)}"
        if re.search(r"(?m)^scale:", fm):
            fm = re.sub(r"(?m)^scale:.*$", lambda _m: f"scale: {scale}", fm)
            if re.search(r"(?m)^scale_reason:", fm):
                fm = re.sub(r"(?m)^scale_reason:.*$",
                            lambda _m: f"scale_reason: {json.dumps(one_line)}", fm)
            else:
                fm = re.sub(r"(?m)^scale:.*$", lambda _m: pair, fm)
        else:
            fm = re.sub(r"(?m)^kind:(.*)$", lambda mm: f"kind:{mm.group(1)}\n{pair}", fm, count=1)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_fanout(self, dev_root: Path, item_id: str, fanout: str) -> bool:
        """Set a research item's `fanout` — whether triage judged the surface to need
        SPLITTING. No reason field: `scale_reason` already carries the sizing argument."""
        from .kind_profiles import ITEM_FANOUT
        if fanout not in ITEM_FANOUT:
            raise ValueError(f"fanout must be one of {'/'.join(ITEM_FANOUT)} (got {fanout!r})")
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _, body = _parse_md(text)
        fm = m.group(1)
        line = f"fanout: {fanout}"
        if re.search(r"(?m)^fanout:", fm):
            fm = re.sub(r"(?m)^fanout:.*$", lambda _m: line, fm)
        elif re.search(r"(?m)^scale_reason:", fm):
            fm = re.sub(r"(?m)^scale_reason:(.*)$",
                        lambda mm: f"scale_reason:{mm.group(1)}\n{line}", fm, count=1)
        else:
            fm = re.sub(r"(?m)^kind:(.*)$", lambda mm: f"kind:{mm.group(1)}\n{line}", fm, count=1)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_research_kind(self, dev_root: Path, item_id: str, research_kind: str,
                                    reason: str) -> bool:
        """Set a research item's investigation family plus the one line behind
        it. The label decides which guide investigate reads.

        LOUD where scale's writer is forgiving: writing a family onto an implementation item is a field
        nobody would ever read."""
        from .kind_profiles import RESEARCH_KINDS
        if research_kind not in RESEARCH_KINDS:
            raise ValueError(
                f"research_kind must be one of {'/'.join(RESEARCH_KINDS)} (got {research_kind!r})")
        if not (reason or "").strip():
            raise ValueError("research_kind needs a one-line reason — it is what the owner "
                             "argues with")
        one_line = " ".join(str(reason).split())
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _, body = _parse_md(text)
        fm = m.group(1)
        if not re.search(r"(?m)^kind:\s*research\s*$", fm):
            raise ValueError("only a research item has an investigation family — this item's kind "
                             "is not `research`")
        # Lambda replacements throughout: `json.dumps` emits \uXXXX for non-ASCII and `re.sub`
        # parses escapes in a replacement STRING (see set_work_item_scale).
        pair = (f"research_kind: {research_kind}\n"
                f"research_kind_reason: {json.dumps(one_line)}")
        if re.search(r"(?m)^research_kind:", fm):
            fm = re.sub(r"(?m)^research_kind:.*$",
                        lambda _m: f"research_kind: {research_kind}", fm)
            if re.search(r"(?m)^research_kind_reason:", fm):
                fm = re.sub(r"(?m)^research_kind_reason:.*$",
                            lambda _m: f"research_kind_reason: {json.dumps(one_line)}", fm)
            else:
                fm = re.sub(r"(?m)^research_kind:.*$", lambda _m: pair, fm)
        elif re.search(r"(?m)^scale_reason:", fm):
            fm = re.sub(r"(?m)^scale_reason:(.*)$",
                        lambda mm: f"scale_reason:{mm.group(1)}\n{pair}", fm, count=1)
        else:
            fm = re.sub(r"(?m)^kind:(.*)$", lambda mm: f"kind:{mm.group(1)}\n{pair}", fm, count=1)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_triaged(self, dev_root: Path, item_id: str) -> bool:
        """Stamp `triaged_at` — what the triage-exit gate reads, instead of a
        `kind set + body filled` tautology any push satisfies."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if meta.get("triaged_at"):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = m.group(1)
        stamp = date.today().isoformat()
        if re.search(r"(?m)^triaged_at:", fm):
            fm = re.sub(r"(?m)^triaged_at:.*$", f"triaged_at: {stamp}", fm)
        else:
            fm = re.sub(r"(?m)^created_at:", f"triaged_at: {stamp}\ncreated_at:", fm, count=1)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {stamp}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_terminal(self, dev_root: Path, item_id: str, outcome: str = "completed",
                               *, superseded_by: str | None = None) -> bool:
        """Move a work-item to its TERMINAL state — a status change, never a delete.
        `superseded` REQUIRES `superseded_by`."""
        if outcome not in ("completed", "abandoned", "superseded"):
            raise ValueError(f"unknown terminal outcome {outcome!r}")
        if outcome == "superseded" and not superseded_by:
            raise ValueError("superseded requires superseded_by")
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if meta.get("done_at") or str(meta.get("status")) == "done":
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        today = date.today().isoformat()
        fm = m.group(1)

        def _upsert(field: str, value: str, block: str) -> str:
            if re.search(rf"(?m)^{field}:.*$", block):
                return re.sub(rf"(?m)^{field}:.*$", f"{field}: {value}", block)
            return block.rstrip() + f"\n{field}: {value}"

        fm = _upsert("status", "done", fm)
        fm = _upsert("outcome", outcome, fm)
        fm = _upsert("done_at", today, fm)
        if superseded_by:
            fm = _upsert("superseded_by", superseded_by, fm)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {today}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        # Terminal is where working space stops being anyone's. This is the only line that removes
        # scratch.
        sandbox.prune_scratch(item.parent, only_if_empty=False)
        return True

    def write_artifact(self, dev_root: Path, item_id: str, name: str, text: str) -> bool:
        """Write a file into a work-item's `artifacts/`, creating the folder. True if the
        item folder exists."""
        folder = Path(dev_root) / "work-items" / item_id
        if not folder.is_dir():
            return False
        adir = folder / "artifacts"
        adir.mkdir(exist_ok=True)
        (adir / name).write_text(text)
        return True

    def _set_item_field(self, dev_root: Path, item_id: str, key: str, value: str,
                        after: tuple[str, ...] = ()) -> bool:
        """Write ONE frontmatter field in place, inserted after the first `after` key
        present. Line-based, so shape and comments survive.

        The run-config setters below are this with a different key; written out per key, two of them
        drifted."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get(key)) == str(value):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = m.group(1)
        if re.search(rf"(?m)^{key}:", fm):
            fm = re.sub(rf"(?m)^{key}:.*$", f"{key}: {value}", fm)
        else:
            anchor = next((a for a in after if re.search(rf"(?m)^{a}:", fm)), None)
            if anchor:
                fm = re.sub(rf"(?m)^({anchor}:.*)$", rf"\1\n{key}: {value}", fm)
            else:
                fm = fm.rstrip() + f"\n{key}: {value}"
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_model(self, dev_root: Path, item_id: str, model: str) -> bool:
        """Set a work-item's configured `model`, stored as its TIER ALIAS. The concrete
        latest resolves at consumption, so a pick auto-tracks."""
        from .models import model_family
        return self._set_item_field(dev_root, item_id, "model",
                                    model_family(model) or model, after=("status",))

    def set_work_item_effort(self, dev_root: Path, item_id: str, effort: str) -> bool:
        """Set a work-item's configured reasoning `effort` (low|medium|high) — used by its runs
        (plan + bound chat)."""
        return self._set_item_field(dev_root, item_id, "effort", effort, after=("model", "status"))

    # The two roles that do NOT run on the item's tier. Absent falls through; nothing means "same
    # as this item".
    ROLE_FIELDS: tuple[str, ...] = ("vet", "deputy")

    def set_work_item_role_model(self, dev_root: Path, item_id: str, role: str, model: str) -> bool:
        """Set this item's model for one ROLE (`vet_model` / `deputy_model`), as a tier alias."""
        from .models import model_family
        if role not in self.ROLE_FIELDS:
            raise ValueError(f"unknown run role '{role}'")
        return self._set_item_field(dev_root, item_id, f"{role}_model",
                                    model_family(model) or model, after=("effort", "model", "status"))

    def set_work_item_role_effort(self, dev_root: Path, item_id: str, role: str, effort: str) -> bool:
        """Set this item's reasoning effort for one ROLE (`vet_effort` / `deputy_effort`)."""
        if role not in self.ROLE_FIELDS:
            raise ValueError(f"unknown run role '{role}'")
        return self._set_item_field(dev_root, item_id, f"{role}_effort", effort,
                                    after=(f"{role}_model", "effort", "model", "status"))

    def set_work_item_session(self, dev_root: Path, item_id: str, session_id: str | None,
                              slot: str = "triage") -> bool:
        """Persist a session id onto a work-item's SLOT — one per phase, plus `build`
        and `vet`.

        Writing any slot NULLs the legacy `session_id`, so a stale value cannot shadow the others.
        A retired slot can be read but never written."""
        from .kind_profiles import SESSION_SLOTS
        if slot not in SESSION_SLOTS:
            raise ValueError(f"unknown session slot {slot!r} — known: {SESSION_SLOTS}")
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        key = f"session_{slot}"
        if str(meta.get(key)) == str(session_id) and not meta.get("session_id"):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        today = date.today().isoformat()
        sid = json.dumps(session_id) if session_id else "null"
        fm = m.group(1)
        if re.search(rf"(?m)^{key}:", fm):
            fm = re.sub(rf"(?m)^{key}:.*$", f"{key}: {sid}", fm)
        else:  # insert the slot next to its siblings — right before created_at (always present)
            fm = re.sub(r"(?m)^created_at:", f"{key}: {sid}\ncreated_at:", fm, count=1)
        fm = re.sub(r"(?m)^session_id:.*$", "session_id: null", fm)
        if key != "session_intake":
            fm = re.sub(r"(?m)^session_intake:.*$", "session_intake: null", fm)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {today}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_handoff_mark(self, dev_root: Path, item_id: str, mark: int) -> bool:
        """Advance the `handoffs_promoted` watermark. Written only AFTER the
        carrying turn landed, so a failed turn re-injects."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        try:
            cur = int(str(meta.get("handoffs_promoted") or 0).strip() or 0)
        except (TypeError, ValueError):
            cur = 0
        if cur == int(mark):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = m.group(1)
        if re.search(r"(?m)^handoffs_promoted:", fm):
            fm = re.sub(r"(?m)^handoffs_promoted:.*$", f"handoffs_promoted: {int(mark)}", fm)
        else:
            fm = re.sub(r"(?m)^created_at:", f"handoffs_promoted: {int(mark)}\ncreated_at:",
                        fm, count=1)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def work_item_session_ids(self, item: dict) -> list[str]:
        """Every session id an item holds — for lifecycle paths that must sweep ALL
        its threads, not just the current phase's."""
        out: list[str] = []
        for sid in [*(item.get("sessions") or {}).values(), item.get("session_id")]:
            if sid and sid not in out:
                out.append(str(sid))
        return out

    def set_work_item_status(self, dev_root: Path, item_id: str, status: str) -> bool:
        """Set a work-item's `status` — the runnable-state axis. ORCHESTRATOR-OWNED,
        never the agent's to set."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get("status")) == str(status):
            return False
        # Terminal is FINAL on this axis: a straggler run's status write must never revive a done
        # item.
        if meta.get("done_at") or str(meta.get("status")) == "done":
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = re.sub(r"(?m)^status:.*$", f"status: {status}", m.group(1))
        # Leaving `error` clears the reason with it — the reason exists only to explain a CURRENT
        # stop.
        if status != "error":
            fm = re.sub(r"(?m)^error_reason:.*\n?", "", fm)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_error(self, dev_root: Path, item_id: str, reason: str) -> bool:
        """Stop an item at `error` with the reason. Not `system_fault`: that is a run
        that COMPLETED while our machinery misbehaved."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if meta.get("done_at") or str(meta.get("status")) == "done":
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        # One line, quoted: the frontmatter is line-parsed, so a multi-line reason or a bare `:`
        # would corrupt it.
        clean = " ".join(str(reason or "the work stopped").split())[:200].replace('"', "'")
        fm = re.sub(r"(?m)^status:.*$", "status: error", m.group(1))
        if re.search(r"(?m)^error_reason:.*$", fm):
            fm = re.sub(r"(?m)^error_reason:.*$", f'error_reason: "{clean}"', fm)
        else:
            fm = fm.rstrip() + f'\nerror_reason: "{clean}"'
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_autopilot(self, dev_root: Path, item_id: str, on: bool) -> bool:
        """Flip the per-item autopilot policy. REMOVED when off, so the frontmatter
        never carries a dead `false`."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if bool(meta.get("autopilot")) == bool(on):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = m.group(1)
        if on:
            fm = re.sub(r"(?m)^(status:.*)$", r"autopilot: true\n\1", fm, count=1)
        else:
            fm = re.sub(r"(?m)^autopilot:.*\n?", "", fm)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_seen(self, dev_root: Path, item_id: str) -> bool:
        """Stamp `seen_at` — the owner opened this drilldown. A read receipt, so
        `updated_at` is deliberately not bumped."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        stamp = datetime.now().isoformat(timespec="seconds")
        fm = m.group(1)
        if re.search(r"(?m)^seen_at:.*$", fm):
            if re.search(rf"(?m)^seen_at: {re.escape(stamp)}$", fm):
                return False
            fm = re.sub(r"(?m)^seen_at:.*$", f"seen_at: {stamp}", fm)
        else:
            fm = fm.rstrip() + f"\nseen_at: {stamp}"
        _meta, body = _parse_md(text)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_scaffold(
        self, dev_root: Path, item_id: str, *, wave: str | None = None, deliverable: str | None = None
    ) -> bool:
        """Set a ROOT item's anchor-scaffold pointer: `wave` or `deliverable`.
        Pass one; the other is cleared to null."""
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

    # Written by the git layer's callers, read by health checks and the FE. A terminal item KEEPS
    # its record.
    _GIT_FIELDS = frozenset({
        "git_branch", "git_worktree", "git_base", "git_merge_commit", "git_merged_at",
        "git_backup_ref",
        # `pr_open` is DERIVED from this stamp, never stored as its own flag — one fact, one
        # field.
        "git_pr_opened_at",
    })

    def set_work_item_git(self, dev_root: Path, item_id: str, **fields) -> bool:
        """Upsert the item's git record. Only known `_GIT_FIELDS` keys are accepted;
        strings are JSON-quoted."""
        bad = set(fields) - self._GIT_FIELDS
        if bad:
            raise ValueError(f"unknown git record fields: {sorted(bad)}")
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists() or not fields:
            return False
        text = item.read_text()
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _meta, body = _parse_md(text)
        fm = m.group(1)

        def _upsert(block: str, key: str, val) -> str:
            rendered = "null" if val is None else json.dumps(val) if isinstance(val, str) else str(val)
            if re.search(rf"(?m)^{key}:", block):
                return re.sub(rf"(?m)^{key}:.*$", f"{key}: {rendered}", block)
            return block.rstrip() + f"\n{key}: {rendered}"

        for key, val in fields.items():
            fm = _upsert(fm, key, val)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    # --- general/ anchor docs ---------------------------------------------------

    def _general_path(self, dev_root: Path, name: str) -> Path | None:
        """Resolve an anchor-doc name to its path (guards the name — no traversal)."""
        if name == "resources":
            return Path(dev_root) / "general" / "resources" / "index.md"
        if name in ANCHOR_DOCS or name in LEGACY_DOCS:
            return Path(dev_root) / "general" / f"{name}.md"
        return None

    def general_docs(self, dev_root: Path) -> list[dict]:
        """The anchor-doc set with presence flags. A legacy doc appears only while it still
        exists on disk."""
        out = []
        for name in (*ANCHOR_DOCS, "resources"):
            p = self._general_path(dev_root, name)
            out.append({"name": name, "present": bool(p and p.exists())})
        for name in LEGACY_DOCS:
            p = self._general_path(dev_root, name)
            if p and p.exists():
                out.append({"name": name, "present": True})
        return out

    def read_general_doc(self, dev_root: Path, name: str) -> str | None:
        """Raw text of one anchor doc, or None (unknown name or missing file)."""
        p = self._general_path(dev_root, name)
        return p.read_text() if (p and p.exists()) else None

    def deliverable_success_signal(self, dev_root: Path, deliverable_id: str) -> str | None:
        """The PRD success-signal lines citing `deliverable_id`, verbatim — the
        owner's own words, and the deputy's acceptance test."""
        if not deliverable_id:
            return None
        prd = self.read_general_doc(dev_root, "project-prd")
        if not prd:
            return None
        section = _section(_strip_fences(prd), "Success signals")
        hits = [ln.strip(" -\t") for ln in section.splitlines()
                if re.search(rf"\b{re.escape(deliverable_id)}\b", ln)]
        return "\n".join(hits) if hits else None

    def project_established(self, dev_root: Path) -> bool:
        """True once this project's memory exists: the PRD defines at least one
        deliverable. Keyed on deliverables, not file presence."""
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

    # The general/ lint: mechanical checks reported as ACTIONABLE FINDINGS. Everything is derived,
    # so it cannot itself go stale.
    _LINT_SEVERITY = {"error": 0, "warn": 1, "info": 2}

    def lint_general(self, dev_root: Path, *, stale_days: int = 90) -> dict:
        """Findings over `general/`: missing docs, undelivered deliverables, dangling ids,
        unanswered questions, stale docs. Worst-first."""
        root = Path(dev_root)
        prd = self.read_general_doc(root, "project-prd") or ""
        deliverables = _parse_deliverables(prd)
        d_ids = {d["id"] for d in deliverables}
        waves = _parse_waves(self.read_general_doc(root, "roadmap") or "")
        items = self._read_work_items(root)
        find: list[dict] = []

        def add(sev: str, kind: str, detail: str, ref: str | None = None) -> None:
            find.append({"severity": sev, "kind": kind, "detail": detail, "ref": ref})

        for name in (*ANCHOR_DOCS, "resources"):
            if name == _LIBRARY_DOC:
                continue                       # an empty verification library is a correct state
            if not (self.read_general_doc(root, name) or "").strip():
                add("warn", "missing-doc", f"{name}.md is empty or absent", name)

        # A retired doc still on disk is a duplication hazard: two files own the same facts.
        for name in LEGACY_DOCS:
            if (self.read_general_doc(root, name) or "").strip():
                add("warn", "retired-doc",
                    f"{name}.md is retired — fold its content into architecture.md and delete it",
                    name)

        # A deliverable nothing is working toward is reported, never auto-fixed.
        claimed = {w["deliverable"] for w in waves} | {
            it.get("deliverable") for it in items if it.get("deliverable")}
        for d in deliverables:
            if d["id"] not in claimed:
                add("info", "deliverable-unclaimed",
                    f"{d['id']} has no wave or work-item delivering it", d["id"])
            for n in d.get("needs") or []:
                if n.lower() != "none" and n not in d_ids:
                    add("error", "broken-needs",
                        f"{d['id']} needs '{n}', which no deliverable defines", d["id"])

        # Success-signal integrity, both directions.
        signals = _section(_strip_fences(prd), "Success signals")
        cited = {i for i in re.findall(r"\bd-[a-z0-9-]+", signals)}
        for d in deliverables:
            if d["id"] not in cited:
                add("warn", "no-success-signal",
                    f"{d['id']} has no success signal — nobody can tell when it's done", d["id"])
        for c in sorted(cited - d_ids):
            add("error", "signal-orphan",
                f"a success signal cites '{c}', which no deliverable defines", c)

        cutoff = datetime.now().timestamp() - stale_days * 86400
        for name in ANCHOR_DOCS:
            if name == _LIBRARY_DOC:
                continue                       # a settled library is proven, not decaying
            p = self._general_path(root, name)
            if p and p.exists() and p.stat().st_mtime < cutoff:
                add("info", "stale-doc", f"{name}.md hasn't changed in over {stale_days} days", name)

        find.sort(key=lambda f: self._LINT_SEVERITY.get(f["severity"], 3))
        return {"findings": find,
                "counts": {s: sum(1 for f in find if f["severity"] == s)
                           for s in ("error", "warn", "info")}}

    def read_portrait(self, dev_root: Path) -> dict:
        """The PORTRAIT — what this project IS, in the six bands the Project view renders.

        Every band maps to exactly ONE doc, so the view can never become a place knowledge secretly lives."""
        root = Path(dev_root)
        prd = _strip_fences(self.read_general_doc(root, "project-prd") or "")
        arch = _strip_fences(self.read_general_doc(root, "architecture") or "")
        caps = _strip_fences(self.read_general_doc(root, "capabilities") or "")
        res = _strip_fences(self.read_general_doc(root, "resources") or "")

        ident = _kv_bullets(_section(prd, "Identity"))
        # Delivered state comes from the roadmap rollup — project-level truth, not the work-item
        # detail behind it.
        board = self.roadmap_board(root)
        delivered = {d["id"] for d in board["deliverables"]
                     if d["rollup"]["total"] and d["rollup"]["done"] >= d["rollup"]["total"]}
        return {
            "identity": {
                "name": _project_name(prd) or Path(root).name,
                "one_liner": _first_para(prd),
                "who": ident.get("who it's for") or ident.get("who"),
                "why": ident.get("why it exists") or ident.get("why"),
            },
            "goals": {
                "now": _bullets(_section(prd, "Goals")),
                "direction": _bullets(_section(prd, "Direction")),
                "non_goals": _bullets(_section(prd, "Non-goals")),
            },
            # Present tense ONLY. An empty list is the honest answer for a project that has not shipped.
            "capabilities": [
                {"name": _deemph(m.group("id")), "detail": _deemph(m.group("title"))}
                for m in (_DELIVERABLE_RE.match(ln) for ln in
                          _section(caps, "Capabilities").splitlines()) if m],
            "build": {
                "stack": _kv_list(_section(arch, "Stack")),
                "invariants": _bullets(_section(arch, "Invariants")),
                "not_here": _bullets(_section(arch, "What's deliberately not here")),
            },
            "deliverables": [
                {"id": d["id"], "value": d["value"], "title": d["title"],
                 "delivered": d["id"] in delivered}
                for d in _parse_deliverables(prd)],
            # Resources are authored as `- **Label**: pointer` or as plain bullets. Accept both.
            "resources": _kv_list(res) or [{"key": "", "value": v} for v in _bullets(res)],
        }

    def roadmap_board(self, dev_root: Path, items: list[dict] | None = None) -> dict:
        """Join the anchor scaffold with live work-items: deliverable → wave → items, plus
        rollup. `orphans` surfaces breaks."""
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

    def orient_digest(self, dev_root: Path, items: list[dict] | None = None, *,
                      in_progress: bool = True) -> str | None:
        """The thin always-on ORIENT line. Kept tiny — it is a permanent per-turn cost.
        None when there is nothing yet."""
        prd = self.read_general_doc(dev_root, "project-prd")
        line = _first_para(prd) if prd else None
        board = self.roadmap_board(dev_root, items)
        active = [(w["title"], w["deliverable"]) for d in board["deliverables"]
                  for w in d["waves"] if w.get("status") == "active"]
        if items is None:
            items = self._read_work_items(Path(dev_root) / "work-items")
        # A `close`-phase item is done-pending-owner: otherwise a finished cohort drowns the real
        # signal.
        inprog = [it for it in items
                  if it.get("status") in _LIVE_STATUSES and not it.get("done_at")
                  and str(it.get("phase")) != "close"]
        if not (line or active or inprog):
            # Make the empty state VISIBLE, not silent, so the charter's no-memory rule fires.
            return ("**No project memory yet.** Establishing it is your first task — project-init for a "
                    "new/empty repo, retrofit for existing code (see the charter). Don't build until it exists.")
        out: list[str] = []
        if line:
            out.append(line)
        if active:
            out.append("Active: " + " · ".join(f"{t} ({d})" for t, d in active))
        if inprog and in_progress:
            out.append("In progress: " + " · ".join(
                f"{it.get('title') or it.get('id')} [{it.get('phase')}]" for it in inprog[:6]))
        return "\n".join(out)

    def delete_work_item(self, dev_root: Path, item_id: str) -> bool:
        """Hard-delete a work-item folder and any branch-offs nested under it. The caller
        enforces the phase guard."""
        folder = Path(dev_root) / "work-items" / item_id
        if not folder.is_dir():
            return False
        shutil.rmtree(folder)
        return True

    # A KEEPLIST, so an unclassified field is DROPPED — identity, relations, the ask, and the
    # owner's configuration survive.
    _RERUN_KEEP = (
        # identity — a new id would re-point every edge and orphan the permanent run trace.
        "id", "root_id", "parent_id", "title", "kind", "created_at",
        # relations — the work-graph edges. Losing one of these wedges the scheduler.
        "spawned_from", "after", "cohort", "wave", "deliverable", "inbox_id",
        # the owner's configuration of this item, which a re-run is not a decision to revisit
        "autopilot", "prompt_extraction", "model", "effort",
        # the code line. The branch is KEPT; only the worktree dir is torn down and re-added.
        "git_branch", "git_base",
    )

    def reset_work_item(self, dev_root: Path, item_id: str) -> dict | None:
        """Reset a work-item to its entry phase, keeping identity, relations and the ask —
        the file half of a re-run.

        `preliminary/` STAYS: it is the pushed input, not work this item did. Runs and events are
        permanent trace."""
        from .kind_profiles import get_profile
        folder = Path(dev_root) / "work-items" / item_id
        item_md = folder / "item.md"
        if not item_md.exists():
            return None
        meta, body = _parse_md(item_md.read_text())
        if not meta:
            return None
        kept = {k: meta[k] for k in self._RERUN_KEEP if k in meta}
        phase = get_profile(str(meta.get("kind") or "implementation")).phases[0]
        after_ids = [str(a) for a in (meta.get("after") or []) if a]
        status = "active"
        if any(str((self.read_work_item(dev_root, a) or {}).get("status")) != "done"
               for a in after_ids):
            status = "awaiting_upstream"
        today = date.today().isoformat()

        def _render(val) -> str:
            if val is None:
                return "null"
            if isinstance(val, bool):
                return "true" if val else "false"
            if isinstance(val, (dict, list)):
                return json.dumps(val)
            if isinstance(val, str):
                return json.dumps(val)
            return str(val)

        lines = [f"{k}: {_render(v)}" for k, v in kept.items()]
        # No re-run counter: it existed only to explain files younger than their run history, and
        # the soft delete ended that.
        lines += [f"phase: {phase}", f"status: {status}", "done_at: null", "artifacts: []",
                  "session_id: null", f"updated_at: {today}"]
        item_md.write_text("---\n" + "\n".join(lines) + "\n---\n" + body.lstrip("\n"))

        removed = []
        # `scratch/` goes too: last attempt's half-built inventories are exactly the stale input a
        # fresh run must not find.
        for name in ("artifacts", "reports", "checkpoints", sandbox.SCRATCH_DIRNAME):
            sub = folder / name
            if sub.is_dir():
                shutil.rmtree(sub)
                removed.append(name + "/")
        for name in ("artifacts", "reports"):
            (folder / name).mkdir(parents=True, exist_ok=True)

        log_file = folder / "deputy-log.jsonl"
        if log_file.exists():
            log_file.unlink()
            removed.append(log_file.name)
        return {"phase": phase, "status": status, "removed": removed}



    # --- internals --------------------------------------------------------------

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
            meta, body = _parse_md(item_md.read_text())
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
