"""DevKnowledgeService — read the durable, file-based part of a context's `dev/` subtree.

Walks `work-items/` and parses markdown plus YAML frontmatter into the item tree the dashboard
renders, computing the derived view (blocked, root/children, glance) — never stored.

The inbox is not a file; it is a queue in DevStore, and the caller passes its rows into
`read_all` so the glance sees one combined picture.

Surface-agnostic: it operates on a `dev_root` Path and never knows who is calling.
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
    """Write the knowledge home's `.gitignore` if it has none.

    At the ROOT rather than per item: a knowledge home may be given its own remote, and the rule
    belongs once where that remote would read it. Never raises — the next mint retries."""
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
    """A work-item's session slots (`session_<slot>`) plus the COMPUTED `session_id` — the
    session for the item's CURRENT phase, so every single-session consumer keeps reading "the item's
    thread" and it follows the phase.

    Two legacy fallbacks, both self-healing on the next slot write: the shared `session_intake` slot,
    and the bare `session_id` of a pre-roles item. Neither is written again."""
    from .kind_profiles import (INTAKE_PHASES, LEGACY_INTAKE_SLOT, SESSION_SLOTS, session_slot)
    keys = (*SESSION_SLOTS, LEGACY_INTAKE_SLOT)
    sessions = {s: str(meta[f"session_{s}"]) for s in keys if meta.get(f"session_{s}")}
    phase = str(meta.get("phase") or "triage")
    try:
        slot = session_slot(phase)
    except KeyError:
        slot = "triage"  # unknown/legacy phase label — never blow up a read
    legacy_intake = sessions.get(LEGACY_INTAKE_SLOT) if slot in INTAKE_PHASES else None
    legacy_id = meta.get("session_id")
    computed = (sessions.get(slot) or legacy_intake
                or (str(legacy_id) if legacy_id else None))
    return sessions, computed


# Display order. Research's mid-pipeline stages rank beside their implementation counterparts.
# Status ranks put what NEEDS THE OWNER first, then runnable work, then routed waits, then done.
_PHASE_RANK = {"triage": 0, "plan": 1, "build": 2, "investigate": 2,
               "vet": 3, "review": 4, "close": 5}
# `error` outranks even awaiting_human: an item whose work STOPPED is a louder claim than one
# resting at a gate by design (recovery-resilience R2).
_STATUS_RANK = {"error": 0, "awaiting_human": 1, "active": 2, "awaiting_child": 3,
                "awaiting_upstream": 4, "awaiting_slot": 4, "done": 5}
# Non-terminal. A parked item IS live: nothing is asked of the owner, but the work is queued.
# `error` is live too — it is work waiting to be resumed, and a dead-end item is what that
# status exists to prevent.
_LIVE_STATUSES = ("active", "awaiting_child", "awaiting_upstream", "awaiting_slot",
                  "awaiting_human", "error")
_SPAWN_RELATIONS = ("blocking", "parallel", "spawn")


def _toposort_keys(specs: list[dict]) -> list[str]:
    """Order batch keys so every intra-batch `after` dependency comes BEFORE its dependent.
    Cross-batch refs impose no order — they exist on disk already.

    Raises naming the cycle if the edges are not a DAG: a launch with a circular dependency can never
    make progress."""
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
    """Normalize one `artifacts` entry to the single `{type, path}` shape. Agents have written
    either a bare path or the dict, so this collapses both at the read boundary. `type` defaults to
    the filename stem."""
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


# The anchor docs carry NO frontmatter. Deliverables and waves live as id-tagged lists in the
# body. These parsers are forgiving, and ``` fences are skipped so examples do not parse as data.
#
# The set is split by the QUESTION each answers, one owner per fact:
#   project-prd    what is this · who for · why · deliverables as value
#   architecture   how it is built · stack · invariants · what is deliberately absent
#   capabilities   what it can do RIGHT NOW — present tense only, never the plan
#   decisions      why we chose what we chose (append-only + supersede)
#   roadmap        what is coming (forward-only)
#   resources      where the external things are
#
# `spec` was retired into architecture: keeping both guaranteed duplication. It stays readable
# via LEGACY_DOCS until existing repos fold it in.
ANCHOR_DOCS = ("project-prd", "architecture", "capabilities",
               "decisions", "roadmap", "verification")   # + resources/index.md
# The repo's verification library. An anchor doc so the owner edits it where they read the
# others, but exempt from the lint: an empty library is the correct starting state.
_LIBRARY_DOC = "verification"
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


# Anchor docs are prose an agent writes, not a form it fills, so every reader here degrades to
# an empty result. A band the docs do not answer simply does not render.
_KV_RE = re.compile(r"^\s*-\s+\*\*(?P<k>[^*]+?)\*\*\s*[:—–-]\s*(?P<v>.+?)\s*$")
# Colon-only, to tell STRUCTURE from a sentence that merely opens in bold. Treating prose with
# a bold lead as a field would silently drop it from its band.
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


# Optional indented sub-fields. Kept as sub-bullets rather than folded into the title line, so
# the machine-parsed format stays byte-identical and every existing PRD keeps working.
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


# An open question is an ADDRESSABLE record, not prose. The id is what lets the owner answer it
# from the dashboard instead of opening the file in an IDE.
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
        """Attach run telemetry to each work-item, in place.

        The daemon owns the spine and passes the run data in as plain dicts, keeping this service free of
        any spine dependency."""
        for it in items:
            wid = it.get("id")
            s = stats.get(wid, {})
            it["total_tokens"] = s.get("total_tokens", 0)
            # Both bases recorded: `phase_tokens` is the 3-type figure the card shows, `_4type` the full
            # volume behind it.
            by_phase = dict(s.get("by_phase", {}))
            by_phase_cr = dict(s.get("by_phase_cr", {}))
            # Legacy runs carry no phase. Attribute them to the item's CURRENT one: every new run is
            # stamped, so this only ever re-homes legacy spend, and is exact for a never-advanced item.
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
                 # Epoch seconds, like `run_started_at` — the surface renders the elapsed itself, so
                 # "3 minutes ago" keeps counting between polls instead of freezing at fetch time.
                 "ended_at": _iso_epoch(s.get("last_ended_at"))}
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
            # Live run's, else the item's CONFIGURED model, else the last run's. Configured beats
            # telemetry, so a reconfigured item shows what it WILL use.
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
        """Stamp a new top-level work-item from a pushed inbox row, entering at triage/active.

        `kind` is validated and REQUIRED with no default: an unknown kind must never resolve silently,
        and a default here gave every ordinary ticket an implementation pipeline because nobody was
        asked. `proposed_kind` is birth provenance, never routed on — triage reads it once to tell
        "nobody judged" from "someone judged and I disagree".

        `spawned_from` is the provenance edge, written child-side only. `after` is peer sequencing, and
        an item with any non-terminal upstream enters `awaiting_upstream`. Peers are validated to EXIST:
        a typo'd id that silently parks work forever is the worst failure this edge can have.

        The id is an OPAQUE token, fully decoupled from the title."""
        from .kind_profiles import DEFAULT_SCALE, KIND_PROFILES, RESEARCH_KINDS, get_profile
        profile = get_profile(kind)  # loud KeyError on unknown kind, before any disk write
        # A button-launched sweep is born classified and past triage: the button IS the
        # classification, and there is no ticket to read. Every other mint point enters at phase 0,
        # because a kind is a PROPOSAL until triage confirms it.
        # Validated before any disk write, so a caller cannot mint into a phase the kind lacks.
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
        # Peer edges are validated against disk BEFORE any write: a mistyped upstream id would
        # park this item at awaiting_upstream with nothing that can ever release it.
        after_ids = [str(a) for a in (after or []) if a]
        missing = [a for a in after_ids if not (wi / a / "item.md").exists()]
        if missing:
            raise ValueError(f"after references unknown work-item(s): {', '.join(missing)}")
        # The FLOOR under every mint point: a title that already reads as a label is untouched; one
        # that is really the ask degrades to its first sentence. Agents are bounced instead, at their
        # own tool, so they learn — a human cannot be.
        title = normalize_title(title, description=description)
        wid = secrets.token_hex(6)                       # opaque 12-hex id == folder name (~2^48)
        while (wi / wid).exists():                       # vanishingly rare live clash → re-roll
            wid = secrets.token_hex(6)
        folder = wi / wid
        # BOTH homes exist from birth. `reports/` used to be created by whoever wrote the first
        # report — which made triage reach for `mkdir -p`, a mutation the boundary check does not
        # auto-allow (it keys on the session's cwd, and triage sits at the repo), so the phase was
        # denied a directory inside its own item and had to work around it (live, 2026-08-07).
        # An item's own folders are the kernel's to make, not an agent's.
        #
        # `scratch/` is deliberately NOT among them: it is a run's working space, so it is made
        # when a run is told about it and swept when unused (see core/sandbox). Minting it here
        # gave every item an empty directory the owner had to scroll past.
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
        # What the FILER proposed this item's kind was — written only when somebody actually said
        # (absent reads null: nobody judged, which is what every item minted before this field is).
        # Frozen at birth and read by exactly one reader, triage: agreeing with it is silent, and
        # overruling it is the owner's call, not the agent's (set_triage_classification).
        if proposed_kind:
            extra += f"proposed_kind: {proposed_kind}\n"
        # Autopilot is a per-item POLICY (not a run-state): does the workflow drive itself through
        # this item's gates, or wait for a click at each. Written only when on (absent reads False —
        # no dead `autopilot: false` lines on the hand-driven majority). Onboarding items are born
        # with it on; any item can be switched at the inbox stage (the last cheap moment).
        if autopilot:
            extra += "autopilot: true\n"
        # Throwaway prompt-extraction item (prompt-inspector): a disposable item minted only to run a
        # real lifecycle so we CAPTURE its actual per-phase input prompts, then torn down. Written
        # only when on (absent reads False). Gates capture-only-for-throwaway, merge/anchor-write
        # suppression, the deterministic review pass-through, and the post-close self-cleanup.
        if prompt_extraction:
            extra += "prompt_extraction: true\n"
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
            # Born `standard` — the shape everything already has. Triage judges it (with a reason)
            # before the item leaves the first phase; see kind_profiles.ITEM_SCALES.
            f"scale: {DEFAULT_SCALE}\nscale_reason: null\n"
            # Born unjudged — there is no default family (kind_profiles.RESEARCH_KINDS). Triage
            # names one on a research item; on an implementation item it stays null forever. The
            # exception is a button-launched sweep, where the owner named it by pressing it.
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
        """Create a LAUNCH COHORT — the batch of autopilot items an onboarding settles into. Every
        item is born `autopilot=true` under one shared `cohort` id.

        `key` is a caller-local HANDLE for expressing edges within the batch; the real opaque ids are
        minted here, so a caller that does not know ids yet can still wire the PRD's `Needs` graph."""
        if not items:
            raise ValueError("itemize_launch needs at least one item")
        specs: list[dict] = []
        seen: set[str] = set()
        for i, it in enumerate(items):
            key = str(it.get("key") or "").strip()
            title = str(it.get("title") or "").strip()
            if not key:
                raise ValueError(f"item #{i} is missing a `key` (its batch-local edge handle)")
            # Itemization is the one mint point where a whole cohort is named in a single act, so
            # it is where naming discipline is worth enforcing rather than repairing: the caller is
            # an agent, and the complaint lands in its turn as a retry it can act on.
            if (bad := check_title(title, description=str(it.get("description") or ""))):
                raise ValueError(f"item {key!r}: {bad}")
            if key in seen:
                raise ValueError(f"duplicate item key {key!r}")
            seen.add(key)
            # No default kind here either: this is agent-driven creation, and the whole reason the
            # field is asked for is that an itemizer usually KNOWS which it is (a decision with no
            # code is not an implementation item) and used to have no way to say so.
            item_kind = str(it.get("kind") or "").strip()
            if not item_kind:
                raise ValueError(f"item {key!r} is missing a `kind` (implementation | research)")
            specs.append({"key": key, "title": title,
                          "description": str(it.get("description") or ""),
                          "kind": item_kind,
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
            # The caller's kind is BOTH what the item runs as and what it proposed: these items
            # skip the inbox but not triage, so the claim is still a claim, and triage confirming
            # it (or disputing it) works here exactly as it does on a pushed row.
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
        it["prompt_extraction"] = bool(meta.get("prompt_extraction"))   # throwaway probe, absent → False
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

    def set_work_item_title(self, dev_root: Path, item_id: str, title: str) -> bool:
        """Rename a work-item — triage's naming act (the phase that has read the whole ask).

        Safe by construction: the id is an opaque token, the folder name IS the id, and the
        work-graph's parent/root edges derive from folder paths, so no reader anywhere keys on the
        title — it is a label and nothing else. Past log events keep the title they were written
        with, which is correct: they record what the board said at the time.

        Raises ValueError on a title that fails `check_title`, so a bad rename never lands. Returns
        True if the file changed (passing the existing title back is a no-op)."""
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
        # LAMBDA, not an f-string: `re.sub` interprets backslashes in a replacement STRING, and
        # `json.dumps` emits `\uXXXX` for any non-ASCII — so a title with a curly quote raised
        # "bad escape \u" and the rename died. A function replacement is passed through verbatim.
        fm = re.sub(r"(?m)^title:.*$", lambda _m: f"title: {json.dumps(title)}", m.group(1))
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

    def set_work_item_scale(self, dev_root: Path, item_id: str, scale: str,
                            reason: str) -> bool:
        """Set a work-item's `scale` + the one-line `scale_reason` behind it (kind_profiles.
        ITEM_SCALES). Triage's judgment, recorded the way the vet plan's `depth` is: the reason is
        REQUIRED even for `standard`, because it is what the owner reads to disagree with at the
        gate — a bare label is unarguable.

        Items minted before this field existed have no `scale:` line, so the write INSERTS after
        `kind:` rather than assuming a slot. Reading them still works without this (readers default
        via `item_scale`); the insert only matters when triage actually judges one."""
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
        # Every replacement below is a LAMBDA. `re.sub` parses backslash escapes in a replacement
        # STRING, and `json.dumps` writes non-ASCII as `\uXXXX` — so the first live small item died
        # on "bad escape \u" the moment triage wrote a reason containing a typographic quote, and
        # only survived because the agent retried with different wording (run 1084, 2026-08-10).
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
        """Set a research item's `fanout` — whether triage judged this surface to need SPLITTING
        across subagents (kind_profiles.ITEM_FANOUT).

        No reason field, unlike scale and family. Those labels are arguments the owner reads at the
        gate; this one is only ever written to say "and here is why the run you are looking at ran
        one thread" — the sizing argument already lives in `scale_reason`, and a second reason line
        would ask triage to justify the same judgement twice.

        Items minted before this field existed have no line, so the write INSERTS after
        `scale_reason:` (or `kind:`) rather than assuming a slot — readers default via
        `item_fanout`, so an absent value is the family's prescription, not an error."""
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
        """Set a research item's investigation family + the one line behind it
        (kind_profiles.RESEARCH_KINDS). Same contract as `set_work_item_scale` — the reason is
        required because the label alone is unarguable, and this label decides more than scale's
        does: which guide investigate reads, and which artifact shape gets scaffolded.

        LOUD where scale's writer is forgiving. Reading an absent family is fine (nobody judged it);
        WRITING one onto an implementation item is not — it is a field that would never be read
        again, on an item whose phases don't have an investigate step at all.

        Items minted before this field existed have no line, so the write INSERTS after
        `scale_reason:` (or `kind:`) rather than assuming a slot."""
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
        # Terminal is where working space stops being anyone's. Every phase is told "nothing in
        # scratch is kept after this item closes"; this is the line that makes that true, and the
        # only one — nothing else on the close path removes it. Deliberately AFTER the stamp, and
        # idempotent above it: a failed tidy must not cost the item its terminal state.
        sandbox.prune_scratch(item.parent, only_if_empty=False)
        return True

    def write_artifact(self, dev_root: Path, item_id: str, name: str, text: str) -> bool:
        """Write a file into a work-item's `artifacts/` (daemon-side, e.g. the execution snapshot).
        Creates the folder if needed. Returns True if the item folder exists."""
        folder = Path(dev_root) / "work-items" / item_id
        if not folder.is_dir():
            return False
        adir = folder / "artifacts"
        adir.mkdir(exist_ok=True)
        (adir / name).write_text(text)
        return True

    def _set_item_field(self, dev_root: Path, item_id: str, key: str, value: str,
                        after: tuple[str, ...] = ()) -> bool:
        """Write ONE frontmatter field on a work-item, in place. Inserts the key after the first of
        `after` that the file already has (so related keys stay together), else appends it. Bumps
        `updated_at`. Line-based, so the frontmatter's shape and comments survive. Returns True if
        the file changed.

        The four run-config setters below are the same rewrite with a different key; when it was
        written out per key, two of them drifted on where the field lands."""
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
        """Set a work-item's configured `model` (the agent model its runs use — plan + bound
        chat), stored as its TIER ALIAS (`sonnet`) — the canonical on-disk form; the concrete latest
        is resolved at consumption (the run normalizes), so the pick auto-tracks a MODEL_TIERS bump.
        Reconfigurable anytime. Inserts the field if absent (older items predate it)."""
        from .models import model_family
        return self._set_item_field(dev_root, item_id, "model",
                                    model_family(model) or model, after=("status",))

    def set_work_item_effort(self, dev_root: Path, item_id: str, effort: str) -> bool:
        """Set a work-item's configured reasoning `effort` (low|medium|high) — used by its runs
        (plan + bound chat)."""
        return self._set_item_field(dev_root, item_id, "effort", effort, after=("model", "status"))

    # The two roles that do NOT run on the item's own tier: `vet` checks what build produced and the
    # `deputy` judges the gates. Both resolve on their own chain (item → repo-role / system → floor),
    # so these fields are the item-scoped end of it. Absent = fall through to that chain; there is no
    # value meaning "same as this item", because that is the coupling the roles exist to break.
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
        """Persist a session id onto a work-item's SLOT (`session_<slot>` frontmatter key). One slot
        per phase, plus `build` (remembers across cycles) and `vet` (forgets, minted per cycle). The
        item is the durable home of its dev threads; the read layer computes `session_id` (the
        current phase's slot) from these. Writing any slot NULLs the legacy single `session_id` key
        — the slot now owns that thread, and a stale legacy value must not shadow other slots.
        Line-based rewrite preserving frontmatter shape; bumps `updated_at`. Returns True if
        the file changed.

        `slot` is validated against SESSION_SLOTS, so the retired `intake` slot can still be READ
        (see `_session_fields`) but can never be written again."""
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
        if key != "session_intake":   # …and so does the retired pre-split shared slot,
            fm = re.sub(r"(?m)^session_intake:.*$", "session_intake: null", fm)  # adopted once
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
        # Leaving `error` clears the reason with it (R2). A stale "the vet run stopped — upstream
        # was unavailable" line surviving a successful Resume would make the item read broken
        # forever; the reason exists only to explain a CURRENT stop.
        if status != "error":
            fm = re.sub(r"(?m)^error_reason:.*\n?", "", fm)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}")
        return True

    def set_work_item_error(self, dev_root: Path, item_id: str, reason: str) -> bool:
        """Stop an item at `error` with the reason it stopped.

        `error` is NOT `system_fault`: a system fault is a run that COMPLETED while our machinery
        misbehaved, and the work still advanced. This is a run that STOPPED, so the item stays where it
        died until the owner resumes it. Neither is terminal, and neither is a verdict on the work."""
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
        # One line, no colons-then-newlines: the frontmatter is line-parsed, so a multi-line reason
        # would corrupt it. Quoted for the same reason — a bare `:` in the prose would read as YAML.
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
        """Flip the per-item autopilot policy. Written as a bare `autopilot: true`, REMOVED when off,
        so the frontmatter never carries a dead `false`. Callers gate when this is allowed."""
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
        # `strict` repos only (renovation §2.2): the instant the deputy approved and handed the
        # merge to the owner. `pr_open` is DERIVED from it (stamped ∧ not yet merged), never stored
        # as its own flag — one fact, one field, no pair to fall out of step.
        "git_pr_opened_at",
    })

    def set_work_item_git(self, dev_root: Path, item_id: str, **fields) -> bool:
        """Upsert the item's git record. Only known `_GIT_FIELDS` keys are accepted (loud otherwise);
        strings are JSON-quoted so paths survive the line parser."""
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
        """The PRD success-signal lines citing `deliverable_id`, verbatim — the owner's own words
        for what good looks like, and the deputy's review acceptance test.

        Free-form prose, so it matches by id mention rather than a fixed shape. Never raises: the deputy
        escalates when a signal cannot be confirmed."""
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
        """True once this project's memory exists — the PRD defines at least one deliverable.

        Keyed on deliverables rather than file presence, so a fresh repo reads as un-established until
        onboarding fills it, and the workspace routes it to the onboarding front door."""
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
            if name == _LIBRARY_DOC:
                continue                       # an empty verification library is a correct state
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
        """The PORTRAIT — what this project IS, assembled from the anchor docs into the six bands the
        Project view renders.

        Deliberately excludes everything the dashboard already answers. Reading it end to end should tell
        you what the project is in under a minute; that is the only test it has to pass.

        Every band maps to exactly ONE doc, so the view can never become a place knowledge secretly
        lives: it renders what the docs say, or nothing."""
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
            # Resources are authored either as `- **Label**: pointer` or as plain bullets under
            # topic headings; accept both rather than forcing a rewrite of every existing file.
            "resources": _kv_list(res) or [{"key": "", "value": v} for v in _bullets(res)],
        }

    def roadmap_board(self, dev_root: Path, items: list[dict] | None = None) -> dict:
        """Join the anchor scaffold with the live work-items into the board tree: deliverable → wave →
        items plus rollup. `orphans` surfaces referential-integrity breaks."""
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
        """The thin always-on ORIENT line: what this project is, which waves are active, what is in
        progress. Kept tiny — it is a permanent per-turn cost. None when there is nothing yet.

        `in_progress=False` drops the other-items line for a turn already bound to one item. The project
        line still rides: it describes the project, not the queue."""
        prd = self.read_general_doc(dev_root, "project-prd")
        line = _first_para(prd) if prd else None
        board = self.roadmap_board(dev_root, items)
        active = [(w["title"], w["deliverable"]) for d in board["deliverables"]
                  for w in d["waves"] if w.get("status") == "active"]
        if items is None:
            items = self._read_work_items(Path(dev_root) / "work-items")
        # A `close`-phase item is done-pending-owner, so it is excluded: otherwise a finished cohort
        # piles up here and drowns the real signal.
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
        """Hard-delete a work-item folder (item.md, artifacts/ and any branch-offs nested
        under it). Returns True if a folder was removed. Caller enforces any phase
        guard (only pre-build items — triage/plan — are deletable; past that, code may be touched)."""
        folder = Path(dev_root) / "work-items" / item_id
        if not folder.is_dir():
            return False
        shutil.rmtree(folder)
        return True

    # A re-run keeps the item and throws away its WORK, so this is a KEEPLIST: a field nobody
    # classified is DROPPED, which is the safe direction. A clear-list would let a field added
    # next month silently survive, and the item would come back carrying half its old life.
    #
    # KEEP = identity, relations, the original ask, and the owner's configuration.
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
        """Reset a work-item to its entry phase, keeping identity, relations and the ask — the file
        half of a re-run.

        `item.md` is rebuilt from the keeplist with the body untouched; the produced folders go;
        `preliminary/` STAYS, because it is the pushed input rather than work this item did. Runs and
        events are permanent trace and are not this function's business.

        `status` comes back `awaiting_upstream` when any peer is still open — a reset item must no more
        start against unlanded work than a new one."""
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
                return json.dumps(val)     # JSON is valid YAML flow syntax
            if isinstance(val, str):
                return json.dumps(val)
            return str(val)

        lines = [f"{k}: {_render(v)}" for k, v in kept.items()]
        # No re-run counter: it existed only to explain why an item's files were younger than its run
        # history, and the soft delete ended that mismatch.
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
        for name in ("artifacts", "reports"):    # exactly as `create_work_item` leaves it —
            (folder / name).mkdir(parents=True, exist_ok=True)   # `scratch/` waits for a run

        log_file = folder / "deputy-log.jsonl"
        if log_file.exists():
            log_file.unlink()
            removed.append(log_file.name)
        return {"phase": phase, "status": status, "removed": removed}

    # NOTE: the legacy `add_decision` (D-###.md scheme) was retired — decisions are now a
    # `memory` type in the §4.9 knowledge subsystem, not an orphan folder. See PRD §4.9.


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
            it["prompt_extraction"] = bool(meta.get("prompt_extraction"))  # throwaway probe, absent → False
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
            # SHIPPED ≠ TERMINAL: `done` counts everything that ended, abandoned work included, and
            # counting those inflates the one number meaning "this got delivered". Outcome is the
            # discriminator; a pre-outcome item that ended counts as shipped.
            "shipped": sum(1 for it in items
                           if it.get("done_at")
                           and str(it.get("outcome") or "completed") == "completed"),
            "active": active,
            "awaiting_human": awaiting_human,
            "inbox_open": sum(1 for e in inbox if e.get("status", "open") == "open"),
            "counts": {"work_items": len(items)},
        }
