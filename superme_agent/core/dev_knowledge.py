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
import secrets
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


def _session_fields(meta: dict) -> tuple[dict, str | None]:
    """A work-item's session slots (build-vet-loop §1.3): frontmatter keys `session_<role>` →
    `{role: sid}`, plus the COMPUTED `session_id` — the session for the item's CURRENT phase's
    role, so every single-session consumer (FE binding, compaction, phase-close sweep) keeps
    reading "the item's thread" and it now follows the phase. Falls back to the legacy single
    `session_id` key while no slot covers the role (pre-roles items; ws.py adopts it into its
    cwd-implied slot on the next turn)."""
    from .kind_profiles import SESSION_ROLES, session_role
    sessions = {r: str(meta[f"session_{r}"])
                for r in SESSION_ROLES if meta.get(f"session_{r}")}
    try:
        role = session_role(str(meta.get("phase") or "triage"))
    except KeyError:
        role = "intake"  # unknown/legacy phase label — never blow up a read
    legacy = meta.get("session_id")
    computed = sessions.get(role) or (str(legacy) if legacy else None)
    return sessions, computed


# Display order for the work-item lifecycle (workspace-workflow D1/D2). phase = the per-kind
# pipeline stage (union of both kinds' pipelines; see core/kind_profiles.py); the mid-pipeline
# research stages (investigate/report) rank beside their implementation counterparts. status ranks
# put what NEEDS THE OWNER first (awaiting_human), then runnable work, then routed waits, then done.
_PHASE_RANK = {"triage": 0, "plan": 1, "build": 2, "investigate": 2,
               "vet": 3, "report": 3, "review": 4, "close": 5}
_STATUS_RANK = {"awaiting_human": 0, "active": 1, "awaiting_child": 2,
                "awaiting_upstream": 3, "awaiting_slot": 3, "done": 4}
# Statuses that read as "this item is live" (non-terminal). A parked-on-a-peer or queued-for-a-slot
# item IS live — nothing is asked of the owner, but the work is real and queued, so it belongs in
# the board.
_LIVE_STATUSES = ("active", "awaiting_child", "awaiting_upstream", "awaiting_slot", "awaiting_human")
_SPAWN_RELATIONS = ("blocking", "parallel", "spawn")


def _toposort_keys(specs: list[dict]) -> list[str]:
    """Order batch keys so every intra-batch `after` dependency comes BEFORE its dependent (used by
    itemize_launch to create upstreams first). Cross-batch refs to already-existing item ids impose
    no order here (they exist on disk already). Raises ValueError naming the cycle if the edges are
    not a DAG — a launch with a circular dependency can never make progress, so refuse it loudly."""
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
# The doc set is split by the QUESTION each answers, one owner per fact (see
# general_docs/general-knowledge-content-design.md §3):
#   project-prd    what is this · who for · why · goals + non-goals · deliverables as value
#   architecture   how it's built · stack · invariants · what's deliberately not here
#   capabilities   what it can do RIGHT NOW (present tense only — never the plan)
#   decisions      why we chose what we chose (append-only + supersede)
#   roadmap        what's coming (forward-only)
#   resources      where the external things are
# `spec` was RETIRED into architecture: stack/approach/constraints ARE current-state architecture,
# and keeping both guaranteed the duplication we measured on repo-pulse (D-001/2/3 each stated
# twice — as a Stack bullet and as a decision record — so changing one meant remembering the other).
# It stays readable via LEGACY_DOCS so existing repos keep their content until it's folded in.
ANCHOR_DOCS = ("project-prd", "architecture", "capabilities",
               "decisions", "roadmap")            # + resources/index.md
LEGACY_DOCS = ("spec",)                            # readable, lint-flagged, never re-created
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


# --- portrait parsing: the small, forgiving readers behind the Project view ------
# Anchor docs are prose an agent writes, not a form it fills, so every reader here degrades to an
# empty result rather than raising. A band the docs don't answer simply doesn't render.
_KV_RE = re.compile(r"^\s*-\s+\*\*(?P<k>[^*]+?)\*\*\s*[:—–-]\s*(?P<v>.+?)\s*$")
# Colon-only, for deciding what is STRUCTURE vs a sentence that merely opens in bold. `- **Stack**:
# Python` is a field; `- **No CSS framework** — breaks offline rendering` is prose with a bold lead,
# and treating the second as a field would silently drop it from its band.
_KV_STRICT_RE = re.compile(r"^\s*-\s+\*\*[^*]+?\*\*\s*:\s*.+$")
_PLAIN_BULLET_RE = re.compile(r"^\s*-\s+(?P<v>.+?)\s*$")


def _kv_bullets(section: str) -> dict[str, str]:
    """`- **Key**: value` bullets → {lowercased key: value}. Keys are matched case-insensitively
    by callers so an agent writing "Who it's for" or "who it's for" both land."""
    return {_deemph(m.group("k")).lower(): _deemph(m.group("v"))
            for m in (_KV_RE.match(ln) for ln in section.splitlines()) if m}


def _kv_list(section: str) -> list[dict]:
    """Same `- **Key**: value` shape, but ORDER-PRESERVING and repeat-tolerant — for stack rows and
    resource pointers, where the sequence is the author's and duplicate keys are legitimate."""
    return [{"key": _deemph(m.group("k")), "value": _deemph(m.group("v"))}
            for m in (_KV_RE.match(ln) for ln in section.splitlines()) if m]


def _bullets(section: str) -> list[str]:
    """`- text` bullets as plain sentences. A leading `**Bold lead** — rest` is kept (it's emphasis
    inside a sentence, not structure) but a true `- **Key**: value` row is skipped, since `_kv_*`
    owns those and rendering them twice would duplicate the band."""
    return [_deemph(m.group("v"))
            for ln in section.splitlines()
            if (m := _PLAIN_BULLET_RE.match(ln)) and not _KV_STRICT_RE.match(ln)]


def _project_name(prd_text: str) -> str | None:
    """The project's own name from the PRD's `# <name> — …` H1, minus any trailing doc label."""
    m = re.search(r"^#\s+(.+?)\s*$", prd_text or "", re.M)
    return re.split(r"\s+[—–-]\s+", _deemph(m.group(1)))[0] if m else None


# A deliverable may carry OPTIONAL indented sub-fields under its line — `**Value**:` (what the owner
# can do once it lands) and `**Needs**:` (the deliverables it depends on). Kept as sub-bullets rather
# than folded into the title line so the machine-parsed `- **d-x** — Title` format is byte-identical
# to before: `project_established` and every existing PRD keep working untouched.
_D_FIELD_RE = re.compile(r"^\s+-\s+\*\*(?P<key>Value|Needs)\*\*\s*:\s*(?P<val>.+?)\s*$")


def _parse_deliverables(prd_text: str) -> list[dict]:
    """project-prd.md `## Deliverables` → [{id, title, value, needs}] (in document order).

    `value` is None and `needs` [] when the deliverable predates the typed shape — the sub-fields are
    additive, so a v1 PRD parses exactly as it always did."""
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


# An open question is an ADDRESSABLE record, not prose: `- **q-<slug>** — <question> — [OPEN]`, and
# once answered `… — [RESOLVED <date>] <answer>`. The id is what lets the agent say "answer q-window"
# and the owner answer it from the dashboard instead of opening the file in an IDE — the whole point
# of the typed shape (general-knowledge-redesign-input.md §5·B).
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
        """Attach run telemetry to each work-item, in place (R5 push-down, decision #7).

        The daemon owns the spine, so it passes the run data in as plain dicts — `live_by_item`
        (item_id → live running row) and `stats` (item_id → accumulated run stats) — keeping this
        service free of any spine dependency. Adds the live (time/tokens while running) + accumulated
        (total tokens, last run) + display (model, ctx_pct, tasks-progress) fields the cards show.
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
            # never-advanced case — e.g. an item that's lived entirely in one phase shows its full total).
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
                 "model": s.get("last_model"), "ctx_pct": s.get("last_ctx_pct")}
                if s.get("runs") else None
            )
            info = live_by_item.get(wid)
            it["running"] = bool(info)
            it["run_started_at"] = _iso_epoch(info["started_at"]) if info else None
            it["run_tokens"] = info["tokens"] if info else None
            it["run_model"] = info.get("model") if info else None
            it["run_ctx_pct"] = info.get("ctx_pct") if info else None
            # The live run's role (triage/plan/build/vet/review/close/deputy) — lets the chat label
            # the incoming indicator by what's actually running ("Building…" vs "Deputy reviewing…").
            it["run_feature"] = info.get("feature") if info else None
            # The model to show: the live run's, else the item's CONFIGURED model (frontmatter — what
            # its runs use), else the last run's. Configured wins over telemetry so a reconfigured-
            # but-not-yet-rerun item shows what it WILL use.
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
        kind: str = "implementation",
        spawned_from: dict | None = None,
        inbox_id: int | None = None,
        after: list[str] | None = None,
        autopilot: bool = False,
        cohort: str | None = None,
    ) -> dict:
        """Stamp a new top-level work-item from a pushed inbox item.

        Creates `work-items/<id>/{item.md, artifacts/}`, entering at phase=triage, status=active
        (workspace-workflow D1/D2: every item passes triage; kind is a PROPOSAL until the
        triage-exit gate confirms it). `kind` is validated against KIND_PROFILES — an unknown kind
        raises loud (ValueError) with NO folder created. `spawned_from` = the D3 provenance edge
        `{item, relation: blocking|parallel|spawn, note?}` (child-side single write; parent views
        are derived by scan); relation is validated. `inbox_id` = the originating inbox row (D5
        trace; the row itself also stays, with `routed_to` as the reverse pointer). `after` = PEER
        sequencing — ids this item may not start before (populated at itemization from the PRD's
        `Needs` edges). An item created with any non-terminal upstream enters at
        `awaiting_upstream` instead of `active`, and the status router releases it automatically
        when the last one completes; peers are validated to EXIST here, because a typo'd id that
        silently parks work forever is the worst failure this edge can have.

        The id is an OPAQUE token (12-hex), fully DECOUPLED from the title — identity
        is a stable meaningless key, the human label lives only in the `title` field (best-practice
        data modeling; cf. git object hashes — the UI surfaces meaning, not the folder name). The
        folder name IS the id, so the work-graph's parent_id/root_id (derived from folder path parts
        in `_read_work_items`) stay single-sourced and drift-free. Because identity carries nothing
        from the title, a deleted item's id is never regenerated, so a later same-title item can
        NEVER adopt its orphaned (permanent, never-delete-logs) runs/dev-events — the 305k-token
        ghost post-mortem, 2026-07-13. Returns {id, folder}.
        """
        from .kind_profiles import get_profile
        profile = get_profile(kind)  # loud KeyError on unknown kind, before any disk write
        if spawned_from is not None:
            if not isinstance(spawned_from, dict) or not spawned_from.get("item"):
                raise ValueError("spawned_from must be {item, relation[, note]}")
            if spawned_from.get("relation") not in _SPAWN_RELATIONS:
                raise ValueError(f"spawned_from.relation must be one of {_SPAWN_RELATIONS}")
        wi = Path(dev_root) / "work-items"
        wi.mkdir(parents=True, exist_ok=True)
        # Peer edges are validated against disk BEFORE any write: a mistyped upstream id would
        # park this item at awaiting_upstream with nothing that can ever release it.
        after_ids = [str(a) for a in (after or []) if a]
        missing = [a for a in after_ids if not (wi / a / "item.md").exists()]
        if missing:
            raise ValueError(f"after references unknown work-item(s): {', '.join(missing)}")
        title = (title or "").strip()
        wid = secrets.token_hex(6)                       # opaque 12-hex id == folder name (~2^48)
        while (wi / wid).exists():                       # vanishingly rare live clash → re-roll
            wid = secrets.token_hex(6)
        folder = wi / wid
        (folder / "artifacts").mkdir(parents=True, exist_ok=True)

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
        # Autopilot is a per-item POLICY (not a run-state): does the workflow drive itself through
        # this item's gates, or wait for a click at each. Written only when on (absent reads False —
        # no dead `autopilot: false` lines on the hand-driven majority). Onboarding items are born
        # with it on; any item can be switched at the inbox stage (the last cheap moment).
        if autopilot:
            extra += "autopilot: true\n"
        # Launch cohort (autopilot slice 4c): the batch this item was itemized with, at the end of
        # one onboarding. Shared opaque id across the batch; the observability read sums the cohort's
        # aggregate spend. Written only when set (absent reads null — no dead field on the manual
        # majority, which are created one at a time and belong to no cohort).
        if cohort:
            extra += f"cohort: {json.dumps(str(cohort))}\n"
        status = "active"
        if after_ids:
            extra += f"after: {json.dumps(after_ids)}\n"   # JSON is valid YAML flow sequence
            # Park immediately if anything upstream is still open — an item must never spend even
            # one scheduler tick `active` against work that hasn't landed.
            unfinished = [a for a in after_ids
                          if str((self.read_work_item(dev_root, a) or {}).get("status")) != "done"]
            if unfinished:
                status = "awaiting_upstream"
        fm = (
            f"---\nid: {wid}\nroot_id: {wid}\nparent_id: null\n"
            f"title: {json.dumps(title)}\nkind: {profile.kind}\n"
            f"phase: {profile.phases[0]}\nstatus: {status}\n"
            f"done_at: null\nartifacts: []\n{extra}"
            f"session_id: {json.dumps(session_id) if session_id else 'null'}\n"
            f"created_at: {today}\nupdated_at: {today}\n---\n"
        )
        body = (description or "").strip()
        (folder / "item.md").write_text(fm + (body + "\n" if body else ""))
        return {"id": wid, "folder": wid}

    def itemize_launch(self, dev_root: Path, items: list[dict], *,
                       session_id: str | None = None, cohort: str | None = None) -> dict:
        """Create a LAUNCH COHORT — the batch of autopilot work-items an onboarding settles into
        (autopilot slice 4c). Every item is born `autopilot=true` and stamped with one shared
        `cohort` id (aggregate-spend observability groups on it).

        `items` = `[{key, title, description?, kind?, after:[key|id]}]`. `key` is a caller-local
        HANDLE used only to express edges within this batch — the real opaque ids are minted here,
        so the caller (which doesn't know ids yet) can wire the PRD's `Needs` graph by key. Each
        `after` entry is resolved: a batch key → that item's minted id; an existing on-disk id →
        passed through (a cross-cohort dependency); anything else is a loud `ValueError` (a typo'd
        edge that would park work forever is the worst failure this has). Items are created in
        TOPOLOGICAL order so every upstream exists on disk before its dependent — a cycle raises.

        Pure creation, no orchestration: the daemon's launch service fires the first triage run for
        each item that starts `active`; items parked `awaiting_upstream` are kicked by the scheduler
        when their upstreams complete. Returns `{cohort, created:[{id,key,title,after,status}],
        running:[id…], waiting:[id…]}`."""
        if not items:
            raise ValueError("itemize_launch needs at least one item")
        specs: list[dict] = []
        seen: set[str] = set()
        for i, it in enumerate(items):
            key = str(it.get("key") or "").strip()
            title = str(it.get("title") or "").strip()
            if not key:
                raise ValueError(f"item #{i} is missing a `key` (its batch-local edge handle)")
            if not title:
                raise ValueError(f"item {key!r} is missing a title")
            if key in seen:
                raise ValueError(f"duplicate item key {key!r}")
            seen.add(key)
            specs.append({"key": key, "title": title,
                          "description": str(it.get("description") or ""),
                          "kind": str(it.get("kind") or "implementation"),
                          "after": [str(a) for a in (it.get("after") or []) if a]})
        keys = {s["key"] for s in specs}
        for s in specs:                                   # validate every edge BEFORE any write
            for a in s["after"]:
                if a in keys or (Path(dev_root) / "work-items" / a / "item.md").exists():
                    continue
                raise ValueError(f"item {s['key']!r} lists after={a!r}, which is neither another "
                                 f"item in this batch nor an existing work-item")
        order = _toposort_keys(specs)                     # deps-first; raises on a cycle
        cohort_id = str(cohort) if cohort else secrets.token_hex(4)
        key_to_id: dict[str, str] = {}
        created: list[dict] = []
        for key in order:
            s = next(x for x in specs if x["key"] == key)
            after_ids = [key_to_id.get(a, a) for a in s["after"]]   # batch key → id; existing id passes
            wi = self.create_work_item(dev_root, s["title"], s["description"],
                                       session_id=session_id, kind=s["kind"],
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
        """Parse one work-item's `item.md` (frontmatter + body) into a dict, or None if it
        doesn't exist. Lightweight single-item read for callers that need just one item's
        frontmatter (e.g. its `session_id` to resume) without walking the whole tree."""
        p = Path(dev_root) / "work-items" / item_id / "item.md"
        if not p.exists():
            return None
        meta, body = _parse_md(p.read_text())
        it = dict(meta)
        it["id"] = str(meta.get("id") or item_id)
        # Id-like fields are opaque 12-HEX tokens, but one that happens to be all decimal digits
        # parses from YAML as an int (~0.4% of ids — a real 500 in the wild, 2026-07-16). Coerce.
        for k in ("root_id", "parent_id", "superseded_by"):
            if meta.get(k) is not None:
                it[k] = str(meta[k])
        if isinstance(meta.get("after"), (list, tuple)):   # same int-coercion trap, per element
            it["after"] = [str(a) for a in meta["after"] if a]
        it["autopilot"] = bool(meta.get("autopilot"))   # absent → False
        it["cohort"] = str(meta["cohort"]) if meta.get("cohort") else None  # launch cohort (4c)
        it["description"] = body.strip()
        it["sessions"], it["session_id"] = _session_fields(meta)  # role slots + current-role sid
        it["artifacts"] = _norm_artifacts(meta.get("artifacts"))  # legacy str → {type,path} (R5)
        return it

    def _task_lines(self, dev_root: Path, item_id: str) -> list[str]:
        """The item's checklist lines. Single source = plan.md's `## Tasks` section (D6 §1 —
        the living plan IS the task tracker; approve plan = approve breakdown, no drift).
        Falls back to the legacy standalone `tasks.md` for items that predate the workflow."""
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
        """Checklist state → {done, total} (None when there are no task lines). Reads Markdown
        task lines (`- [ ]` / `- [x]`) from plan.md's `## Tasks` — the derived progress the card
        + drilldown progress bar show; never stored."""
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
        """The checklist as an ordered `{text, done}` list (None when empty) — the structured
        form the review popup renders; same lines `task_progress` counts."""
        out: list[dict] = []
        for line in self._task_lines(dev_root, item_id):
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
        """Set a work-item's `phase` (the per-kind pipeline stage; sequencing validity is the
        caller's job via core/kind_profiles.next_phase) — the owner's gate, advanced on approval.
        Line-based rewrite preserving frontmatter shape, bumping `updated_at`. Returns True if
        the file changed."""
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

    def set_work_item_kind(self, dev_root: Path, item_id: str, kind: str) -> bool:
        """Set a work-item's `kind` (D1: a PROPOSAL until the triage-exit gate — this is triage's
        recording surface; the route/tool layer restricts it to the triage phase). Validated
        against KIND_PROFILES (loud KeyError on unknown). Line-based rewrite, bumps updated_at.
        Returns True if the file changed."""
        from .kind_profiles import get_profile
        get_profile(kind)  # loud on unknown kind, before any write
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

    def set_work_item_triaged(self, dev_root: Path, item_id: str) -> bool:
        """Stamp `triaged_at` (F1, playground-e2e-blockers): the triage-exit gate's `triage_ran`
        check reads THIS stamp — written only by triage's recording surface
        (set_triage_classification) — instead of the old `kind set + body filled` tautology,
        which any inbox push already satisfied without a triage agent ever running. Idempotent
        (first stamp wins); line-based rewrite, bumps updated_at. Returns True if the file
        changed."""
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
        """Move a work-item to its TERMINAL state (workspace-workflow D2/D8) — a status change,
        never a delete: `status: done` + `outcome: completed|abandoned|superseded` + `done_at`
        stamp. `superseded` REQUIRES `superseded_by` (no dangling supersedes, D3). Idempotent:
        returns False if already terminal or missing. Human-gated at the route layer — the agent
        never calls this (D8 three-layer close protocol)."""
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
            return False  # already terminal
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

    def set_work_item_session(self, dev_root: Path, item_id: str, session_id: str | None,
                              role: str = "intake") -> bool:
        """Persist a session id onto a work-item's ROLE slot (`session_<role>` frontmatter key —
        build-vet-loop §1.3: intake narrates · build remembers · vet forgets). The item is the
        durable home of its dev threads; the read layer computes `session_id` (current phase's
        role) from these slots. Writing any slot NULLs the legacy single `session_id` key — the
        slot now owns that thread, and a stale legacy value must not shadow other roles' slots.
        Line-based rewrite preserving frontmatter shape; bumps `updated_at`. Returns True if
        the file changed."""
        from .kind_profiles import SESSION_ROLES
        if role not in SESSION_ROLES:
            raise ValueError(f"unknown session role {role!r} — known: {SESSION_ROLES}")
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        key = f"session_{role}"
        if str(meta.get(key)) == str(session_id) and not meta.get("session_id"):
            return False  # unchanged (and no legacy key left to clear) — skip the write
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
        fm = re.sub(r"(?m)^session_id:.*$", "session_id: null", fm)  # slot owns the thread now
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {today}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_handoff_mark(self, dev_root: Path, item_id: str, mark: int) -> bool:
        """Advance the item's `handoffs_promoted` watermark (build-vet-loop §1.4 / step 6) — the
        count of attempts-ledger entries already promoted into the intake thread. Monotonic by
        contract (the ledger is append-only); written only AFTER the turn that carried the
        promotion block landed, so a failed turn re-injects (at-least-once). Line-based rewrite;
        bumps `updated_at`. Returns True if the file changed."""
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
        """Every distinct session id an item holds — all role slots plus a still-unadopted legacy
        `session_id` — for lifecycle paths (complete/abandon/delete) that must sweep/retire ALL of
        an item's threads, not just the current phase's."""
        out: list[str] = []
        for sid in [*(item.get("sessions") or {}).values(), item.get("session_id")]:
            if sid and sid not in out:
                out.append(str(sid))
        return out

    def set_work_item_status(self, dev_root: Path, item_id: str, status: str) -> bool:
        """Set a work-item's `status` (the runnable-state axis — active/awaiting_child/
        awaiting_human; terminal `done` goes through set_work_item_terminal),
        line-based rewrite preserving frontmatter shape, bumping `updated_at`. Status is
        ORCHESTRATOR-OWNED (daemon/routes/status-router), never the agent's to set (D8).
        Returns True if the file changed."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text()
        meta, body = _parse_md(text)
        if str(meta.get("status")) == str(status):
            return False
        # Terminal is FINAL on this axis: a straggler run's end-of-turn status write (an aborted
        # background plan finishing after an abandon, say) must never revive a done item into a
        # ghost `awaiting_human` page. Un-terminal has exactly no path (never-delete lifecycle).
        if meta.get("done_at") or str(meta.get("status")) == "done":
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = re.sub(r"(?m)^status:.*$", f"status: {status}", m.group(1))
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_autopilot(self, dev_root: Path, item_id: str, on: bool) -> bool:
        """Flip the per-item autopilot policy. Written as a bare `autopilot: true` line, REMOVED
        when off (so the frontmatter never carries a dead `autopilot: false`, matching how the
        flag is minted). Bumps `updated_at`. Returns True if the file changed. Callers gate WHEN
        this is allowed (the route restricts it to pre-build — the inbox-stage decision)."""
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
        """Stamp `seen_at` — the owner opened this item's drilldown (S7 attention engine: a
        terminal item without the stamp sits in the `unread` bucket; the stamp clears it).
        A read receipt, so `updated_at` is deliberately NOT bumped. Idempotent (re-stamps).
        Returns True if the file changed."""
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

    # Item-yaml git record fields (workspace-workflow S4/D4) — written by the git layer's
    # callers, read by health checks + the FE. A terminal item KEEPS its record (branch = trace).
    _GIT_FIELDS = frozenset({
        "git_branch", "git_worktree", "git_base", "git_merge_commit", "git_merged_at",
        "git_backup_ref",
    })

    def set_work_item_git(self, dev_root: Path, item_id: str, **fields) -> bool:
        """Upsert the item's git record (S4): branch/worktree at build entry, merge commit +
        backup ref at review. Only the known `_GIT_FIELDS` keys are accepted (loud ValueError
        otherwise); string values are JSON-quoted (paths survive the line parser), None writes
        `null`. Bumps `updated_at`. Returns True if the file changed."""
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
        """The anchor-doc set with presence flags (for the dashboard's Knowledge surface). A legacy
        doc appears only while it still exists on disk — so a repo mid-migration can still open it."""
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
        """The PRD `## Success signals` lines that cite `deliverable_id`, verbatim — the owner's own
        words for "what good looks like" for this deliverable (the deputy's review acceptance test,
        autopilot §2b). Returns the matching bullet(s), or None when the PRD, the section, or a line
        citing the id can't be found. Free-form prose, so it matches by id mention, not a fixed shape
        — a best-effort read, never a raise (the deputy escalates when a signal can't be confirmed)."""
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

    # The general/ lint: mechanical checks over the anchor set, reported as ACTIONABLE FINDINGS
    # rather than a document to read. Everything here is derived — nothing is stored — so it can
    # never itself go stale. Bookkeeping is the 80% of knowledge upkeep humans are worst at; this
    # automates that half and leaves curation (what a fact MEANS) to the owner.
    _LINT_SEVERITY = {"error": 0, "warn": 1, "info": 2}

    def lint_general(self, dev_root: Path, *, stale_days: int = 90) -> dict:
        """Findings over `general/`: missing docs, deliverables that no wave or item is delivering,
        success signals citing an unknown id, deliverables with no success signal, `Needs:` pointing
        at an undefined deliverable, unanswered questions, and stale docs. Ordered worst-first."""
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
            if not (self.read_general_doc(root, name) or "").strip():
                add("warn", "missing-doc", f"{name}.md is empty or absent", name)

        # A retired doc still on disk is a live duplication hazard: two files own the same facts,
        # so one of them silently stops being updated.
        for name in LEGACY_DOCS:
            if (self.read_general_doc(root, name) or "").strip():
                add("warn", "retired-doc",
                    f"{name}.md is retired — fold its content into architecture.md and delete it",
                    name)

        # A deliverable nothing is working toward is either not really planned, or the roadmap
        # forgot it — either way it's the owner's call, so it's a finding rather than a fix.
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

        # Success-signal integrity, both directions (the alirezarezvani AC↔FR invariant).
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
            p = self._general_path(root, name)
            if p and p.exists() and p.stat().st_mtime < cutoff:
                add("info", "stale-doc", f"{name}.md hasn't changed in over {stale_days} days", name)

        find.sort(key=lambda f: self._LINT_SEVERITY.get(f["severity"], 3))
        return {"findings": find,
                "counts": {s: sum(1 for f in find if f["severity"] == s)
                           for s in ("error", "warn", "info")}}

    def read_portrait(self, dev_root: Path) -> dict:
        """The PORTRAIT — what this project IS, assembled from the anchor docs into the six bands the
        Project view renders (identity · goals · capabilities · build · deliverables · resources).

        Deliberately excludes everything the dashboard already answers: decisions (volatile, often
        work-item specific), work in motion, lint findings, status. Reading this end to end should
        tell you what the project is in under a minute — that's the only test it has to pass.

        Every band maps to exactly ONE doc, so the view can never become a place knowledge secretly
        lives: it renders what the docs say or it renders nothing."""
        root = Path(dev_root)
        prd = _strip_fences(self.read_general_doc(root, "project-prd") or "")
        arch = _strip_fences(self.read_general_doc(root, "architecture") or "")
        caps = _strip_fences(self.read_general_doc(root, "capabilities") or "")
        res = _strip_fences(self.read_general_doc(root, "resources") or "")

        ident = _kv_bullets(_section(prd, "Identity"))
        # Deliverables carry their delivered state from the roadmap rollup — project-level truth
        # (this value exists now / doesn't yet), NOT the work-item detail behind it.
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
            # Present tense ONLY. An empty list is the honest answer for a project that hasn't
            # shipped — never a placeholder, never the plan restated (that's the roadmap's job).
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
            # Resources are authored either as `- **Label**: pointer` or as plain bullets under
            # topic headings; accept both rather than forcing a rewrite of every existing file.
            "resources": _kv_list(res) or [{"key": "", "value": v} for v in _bullets(res)],
        }

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
                  if it.get("status") in _LIVE_STATUSES and not it.get("done_at")]
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
        guard (only pre-build items — triage/plan — are deletable; past that, code may be touched)."""
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
            if isinstance(meta.get("after"), (list, tuple)):   # peer edges: ids stay strings
                it["after"] = [str(a) for a in meta["after"] if a]
            it["autopilot"] = bool(meta.get("autopilot"))   # per-item policy, absent → False
            it["cohort"] = str(meta["cohort"]) if meta.get("cohort") else None  # launch cohort (4c)
            it["artifacts"] = _norm_artifacts(meta.get("artifacts"))  # legacy str → {type,path} (R5)
            it["sessions"], it["session_id"] = _session_fields(meta)  # role slots + current-role sid
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
            # The attention list (D10): awaiting_human is the only status that pages the owner.
            if it.get("status") == "awaiting_human":
                awaiting_human.append({"id": it.get("id"), "title": it.get("title")})
        return {
            "by_status": by_status,
            "by_phase": by_phase,
            "active": active,
            "awaiting_human": awaiting_human,
            "inbox_open": sum(1 for e in inbox if e.get("status", "open") == "open"),
            "counts": {"work_items": len(items)},
        }
