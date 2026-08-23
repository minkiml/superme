"""Writing one field of a work-item back to its file, each with its own rule."""

import re
import json
from datetime import date, datetime
from pathlib import Path

from ..vocab import sandbox
from ..vocab.titles import check_title, normalize_title
from .common import _FRONTMATTER, parse_md


class FieldOps:
    def set_work_item_phase(self, dev_root: Path, item_id: str, phase: str) -> bool:
        """Set a work-item's `phase`. Sequencing validity is the caller's job via
        `kind_profiles.next_phase`."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
        if str(meta.get("phase")) == str(phase):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = re.sub(r"(?m)^phase:.*$", f"phase: {phase}", m.group(1))
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_title(self, dev_root: Path, item_id: str, title: str) -> bool:
        """Rename a work-item, triage's naming act.

        Safe by construction: the folder name is the id, so nothing keys on the title."""
        if (bad := check_title(title)):
            raise ValueError(bad)
        title = normalize_title(title)
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
        if str(meta.get("title") or "") == title:
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        # LAMBDA, not an f-string: `re.sub` parses backslashes in a replacement string, and
        # `json.dumps` emits `\uXXXX`.
        fm = re.sub(r"(?m)^title:.*$", lambda _m: f"title: {json.dumps(title)}", m.group(1))
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_kind(self, dev_root: Path, item_id: str, kind: str) -> bool:
        """Record a work-item's `kind` — triage's surface. Validated against
        KIND_PROFILES, loud on unknown."""
        from ..vocab.kind_profiles import get_profile
        get_profile(kind)
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
        if str(meta.get("kind")) == str(kind):
            return False
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        fm = re.sub(r"(?m)^kind:.*$", f"kind: {kind}", m.group(1))
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_scale(self, dev_root: Path, item_id: str, scale: str,
                            reason: str) -> bool:
        """Set `scale` plus the one-line `scale_reason`. The reason is REQUIRED even
        for `standard` — a bare label is unarguable at the gate."""
        from ..vocab.kind_profiles import ITEM_SCALES
        if scale not in ITEM_SCALES:
            raise ValueError(f"scale must be one of {'/'.join(ITEM_SCALES)} (got {scale!r})")
        if not (reason or "").strip():
            raise ValueError("scale needs a one-line reason — it is what the owner argues with")
        one_line = " ".join(str(reason).split())
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_fanout(self, dev_root: Path, item_id: str, fanout: str) -> bool:
        """Set a research item's `fanout` — whether triage judged the surface to need
        SPLITTING. No reason field: `scale_reason` already carries the sizing argument."""
        from ..vocab.kind_profiles import ITEM_FANOUT
        if fanout not in ITEM_FANOUT:
            raise ValueError(f"fanout must be one of {'/'.join(ITEM_FANOUT)} (got {fanout!r})")
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_research_kind(self, dev_root: Path, item_id: str, research_kind: str,
                                    reason: str) -> bool:
        """Set a research item's investigation family plus the one line behind
        it. The label decides which guide investigate reads.

        LOUD where scale's writer is forgiving: writing a family onto an implementation item is a field
        nobody would ever read."""
        from ..vocab.kind_profiles import RESEARCH_KINDS
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
        text = item.read_text(encoding="utf-8")
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_triaged(self, dev_root: Path, item_id: str) -> bool:
        """Stamp `triaged_at` — what the triage-exit gate reads, instead of a
        `kind set + body filled` tautology any push satisfies."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
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
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
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
        (adir / name).write_text(text, encoding="utf-8")
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
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_model(self, dev_root: Path, item_id: str, model: str) -> bool:
        """Set a work-item's configured `model`, stored as its TIER ALIAS. The concrete
        latest resolves at consumption, so a pick auto-tracks."""
        from ..vocab.models import model_family
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
        from ..vocab.models import model_family
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
        from ..vocab.kind_profiles import SESSION_SLOTS
        if slot not in SESSION_SLOTS:
            raise ValueError(f"unknown session slot {slot!r} — known: {SESSION_SLOTS}")
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_handoff_mark(self, dev_root: Path, item_id: str, mark: int) -> bool:
        """Advance the `handoffs_promoted` watermark. Written only AFTER the
        carrying turn landed, so a failed turn re-injects."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_status(self, dev_root: Path, item_id: str, status: str) -> bool:
        """Set a work-item's `status` — the runnable-state axis. ORCHESTRATOR-OWNED,
        never the agent's to set."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_error(self, dev_root: Path, item_id: str, reason: str) -> bool:
        """Stop an item at `error` with the reason. Not `system_fault`: that is a run
        that COMPLETED while our machinery misbehaved."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_autopilot(self, dev_root: Path, item_id: str, on: bool) -> bool:
        """Flip the per-item autopilot policy. REMOVED when off, so the frontmatter
        never carries a dead `false`."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        meta, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_seen(self, dev_root: Path, item_id: str) -> bool:
        """Stamp `seen_at` — the owner opened this drilldown. A read receipt, so
        `updated_at` is deliberately not bumped."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
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
        _meta, body = parse_md(text)
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True

    def set_work_item_scaffold(
        self, dev_root: Path, item_id: str, *, wave: str | None = None, deliverable: str | None = None
    ) -> bool:
        """Set a ROOT item's anchor-scaffold pointer: `wave` or `deliverable`.
        Pass one; the other is cleared to null."""
        item = Path(dev_root) / "work-items" / item_id / "item.md"
        if not item.exists():
            return False
        text = item.read_text(encoding="utf-8")
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _meta, body = parse_md(text)
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
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
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
        text = item.read_text(encoding="utf-8")
        m = _FRONTMATTER.match(text)
        if not m:
            return False
        _meta, body = parse_md(text)
        fm = m.group(1)

        def _upsert(block: str, key: str, val) -> str:
            rendered = "null" if val is None else json.dumps(val) if isinstance(val, str) else str(val)
            if re.search(rf"(?m)^{key}:", block):
                return re.sub(rf"(?m)^{key}:.*$", f"{key}: {rendered}", block)
            return block.rstrip() + f"\n{key}: {rendered}"

        for key, val in fields.items():
            fm = _upsert(fm, key, val)
        fm = re.sub(r"(?m)^updated_at:.*$", f"updated_at: {date.today().isoformat()}", fm)
        item.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
        return True
