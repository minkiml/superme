"""A work-item beginning, ending, or being reset to run again."""

import json
import shutil
import secrets
from datetime import date
from pathlib import Path

from ..vocab import sandbox
from ..vocab.titles import check_title, normalize_title
from .common import _ensure_knowledge_ignore, parse_md
from .parse import _SPAWN_RELATIONS, _toposort_keys


class LifecycleOps:
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
        from ..vocab.kind_profiles import DEFAULT_SCALE, KIND_PROFILES, RESEARCH_KINDS, get_profile
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
        from ..vocab.kind_profiles import get_profile
        folder = Path(dev_root) / "work-items" / item_id
        item_md = folder / "item.md"
        if not item_md.exists():
            return None
        meta, body = parse_md(item_md.read_text())
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
