"""The `general/` anchor docs: the project portrait, the roadmap board, and their lint."""

import re
from datetime import datetime
from pathlib import Path

from .parse import _LIVE_STATUSES, _item_view, _rollup

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


def parse_deliverables(prd_text: str) -> list[dict]:
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
def parse_waves(roadmap_text: str) -> list[dict]:
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


class GeneralOps:
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
        return p.read_text(encoding="utf-8") if (p and p.exists()) else None

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
        return bool(prd and parse_deliverables(prd))

    def write_general_doc(self, dev_root: Path, name: str, text: str) -> bool:
        """Overwrite one anchor doc (creating its folder if needed). False on an unknown name."""
        p = self._general_path(dev_root, name)
        if not p:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return True

    # The general/ lint: mechanical checks reported as ACTIONABLE FINDINGS. Everything is derived,
    # so it cannot itself go stale.
    _LINT_SEVERITY = {"error": 0, "warn": 1, "info": 2}

    def lint_general(self, dev_root: Path, *, stale_days: int = 90) -> dict:
        """Findings over `general/`: missing docs, undelivered deliverables, dangling ids,
        unanswered questions, stale docs. Worst-first."""
        root = Path(dev_root)
        prd = self.read_general_doc(root, "project-prd") or ""
        deliverables = parse_deliverables(prd)
        d_ids = {d["id"] for d in deliverables}
        waves = parse_waves(self.read_general_doc(root, "roadmap") or "")
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
                for d in parse_deliverables(prd)],
            # Resources are authored as `- **Label**: pointer` or as plain bullets. Accept both.
            "resources": _kv_list(res) or [{"key": "", "value": v} for v in _bullets(res)],
        }

    def roadmap_board(self, dev_root: Path, items: list[dict] | None = None) -> dict:
        """Join the anchor scaffold with live work-items: deliverable → wave → items, plus
        rollup. `orphans` surfaces breaks."""
        root = Path(dev_root)
        deliverables = parse_deliverables(self.read_general_doc(dev_root, "project-prd") or "")
        waves = parse_waves(self.read_general_doc(dev_root, "roadmap") or "")
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
