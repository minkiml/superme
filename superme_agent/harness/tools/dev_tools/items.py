"""The work-item itself: scaffolding an artifact, classifying it, banking a checkpoint."""

from typing import Annotated, Literal, Required, TypedDict

from .render import _err, _ok, _s

# Code supplies form, the agent supplies content. These can only touch a work-item's own folder.

def _item_dir(dev_root, item_id: str):
    from pathlib import Path
    if not dev_root:
        return None
    d = Path(dev_root) / "work-items" / str(item_id)
    return d if (d / "item.md").exists() else None


def _bound_err(item_id, bound_item_id) -> str | None:
    """A work-item session operates ONLY its own item.

    Returns the refusal text, or None when the call is in scope."""
    if bound_item_id is None:
        return ("Work-item tools operate only inside a work-item session. This session has no "
                "bound item — if this work is real, itemize it (create_inbox_item) and do it in "
                "the item's own session.")
    if str(item_id) != str(bound_item_id):
        return (f"This session is bound to work-item {bound_item_id!r} and may operate only on "
                f"it — cross-item operations aren't allowed. If item {item_id!r} needs work, that "
                f"happens in ITS session.")
    return None


class ScaffoldArtifactArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id (folder name)"]]
    artifact: Required[Annotated[Literal["brief", "plan", "investigation", "review"],
                                 "which artifact skeleton to scaffold"]]


def _scaffold_artifact(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def scaffold_artifact(args: dict) -> dict:
        from ....core import artifacts as _arts
        from ....core.dev_knowledge import parse_md
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        from pathlib import Path
        from ....core import verification_library as _vl
        meta, _body = parse_md((d / "item.md").read_text(encoding="utf-8"))
        try:
            r = _arts.scaffold(d, _s(args, "artifact"), title=str(meta.get("title") or item_id),
                               item_kind=meta.get("kind"), item_id=item_id,
                               standing=_vl.standing_blocks(Path(dev_root)),
                               # The family picks the artifact shape and is triage's judgment,
                               # never an argument the scaffolding agent passes.
                               research_kind=meta.get("research_kind"))
        except KeyError as e:
            return _err(str(e))
        state = "scaffolded" if r["created"] else "already exists (re-scaffold is a no-op)"
        return _ok(f"{r['path']} {state}. Fill the <fill:…> slots in sections: "
                   f"{', '.join(r['sections']) or '(free-form)'} — the self-check at the consuming "
                   f"gate rejects unfilled slots and empty required sections."
                   + (f" {r['inherited']} standing check(s) from this repo's verification library "
                      "are already in the verification plan — leave them as they are."
                      if r.get("inherited") else ""))
    return scaffold_artifact


class SetTriageClassificationArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    title: Required[Annotated[str, (((((("the name the board should show: a few words, under 60 "
                                         "characters, no period. Fix a weak one; pass a good one "
                                         "back unchanged"))))))]]
    kind: Required[Annotated[Literal["implementation", "research"],
                             (((((("`implementation` = changes code, with a worktree and the vet "
                                   "and review pipeline. `research` = answers a question, read-only "
                                   "on code, delivering findings rather than a merge"))))))]]
    deliverable: Annotated[str, (((((("an existing `d-<slug>` from the project PRD, or omit for a "
                                      "standalone chore. Never invent a slug: a new deliverable is "
                                      "proposed in prose"))))))]
    scale: Required[Annotated[Literal["small", "standard"],
                              (((((("`small` = you can already name the change and where it goes, "
                                    "so later phases stay narrow. `standard` = anything to "
                                    "investigate, more than one area, or a real choice of approach"))))))]]
    fanout: Annotated[Literal["expected", "bounded"],
                      (((((("research only. `expected` = the default, which divides a whole-repo "
                            "sweep across subagents. `bounded` = you looked and it does not divide: "
                            "one folder, one subsystem. A different question from `scale`"))))))]
    scale_reason: Required[Annotated[str, (((((("one line in your own words for why that scale, and "
                                                "what settled it. Required for both values: the "
                                                "owner reads it at the gate"))))))]]
    research_kind: Annotated[Literal["audit", "refactoring", "housekeeping", "security",
                                     "study", "deep-diagnosis"],
                             (((((("required when kind is `research`, rejected otherwise. `audit` = "
                                   "is this surface sound · `refactoring` = what shape should this "
                                   "code be · `housekeeping` = what has gone stale · `security` = "
                                   "what is exposed · `study` = how do others do this · "
                                   "`deep-diagnosis` = the mechanism behind a behaviour we cannot "
                                   "explain. Pick by the question, not the subject"))))))]
    kind_override_reason: Annotated[str, (((((("only when recording a kind that contradicts the one "
                                               "it was filed under, after the owner says which. "
                                               "Quote their answer, or it is refused"))))))]
    research_kind_reason: Annotated[str, (((((("one line for why that family, required alongside "
                                               "`research_kind`. The label picks the method the "
                                               "investigation follows and the shape of its record"))))))]


def _set_triage_classification(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def set_triage_classification(args: dict) -> dict:
        """Triage's RECORDING surface: name, proposed kind and deliverable, written onto the item.

        The exit gate confirms durable fields rather than chat prose."""
        from pathlib import Path
        from ....core.dev_knowledge import DevKnowledgeService, parse_deliverables
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        dev = DevKnowledgeService()
        root = Path(dev_root)
        item = dev.read_work_item(root, item_id) or {}
        if str(item.get("phase")) != "triage":
            return _err("Kind/deliverable are fixed after the triage-exit gate — this item is "
                        f"already in `{item.get('phase')}`. A post-triage need is a branch-off "
                        "(create_inbox_item), never a re-kind.")
        # A kind contradicting the FILED one is triage overruling somebody, so the run stops and
        # asks instead.
        proposed = str(item.get("proposed_kind") or "")
        override = _s(args, "kind_override_reason")
        if proposed and _s(args, "kind") and _s(args, "kind") != proposed and not override:
            return _err(
                f"This item was FILED as {proposed!r}; you are recording {_s(args, 'kind')!r}. "
                "Kind is frozen after this gate, so you may not overrule the filer alone. Nothing "
                "was recorded. End your run with report_completion(machine.outcome='needs_user'), "
                "asking the owner which it is and saying what you saw that disagrees. When they "
                "answer, call this again with `kind_override_reason` quoting their decision.")
        # Checked before any write: this validation spans two fields, so a refusal halfway would
        # leave the item inconsistent.
        fam = _s(args, "research_kind")
        if _s(args, "kind") == "research" and not fam:
            # This list must match the `research_kind` Literal — a refusal tells the agent what to
            # do next.
            return _err("A research item needs `research_kind` (audit | refactoring | "
                        "housekeeping | security | study | deep-diagnosis) + "
                        "`research_kind_reason` — it decides what counts as an "
                        "answer, which guide the investigation follows, and the shape of the "
                        "record it writes. Nothing was recorded; call again with both.")
        if _s(args, "kind") != "research" and fam:
            return _err(f"`research_kind` ({fam!r}) belongs to a research item — this one is "
                        f"`{_s(args, 'kind')}`, and its phases have no investigation step. Nothing "
                        "was recorded; drop the argument or fix the kind.")
        title = _s(args, "title")
        try:
            renamed = dev.set_work_item_title(root, item_id, title)
        except ValueError as e:
            return _err(str(e))
        kind = _s(args, "kind")
        try:
            dev.set_work_item_kind(root, item_id, kind)
        except KeyError as e:
            return _err(str(e))
        if proposed and kind != proposed:
            store.log_event(
                context_id, "item.kind_override",
                f"Kind overruled: filed as {proposed}, recorded as {kind} — {override}",
                item_id=item_id, actor="agent",
                meta={"proposed_kind": proposed, "kind": kind, "reason": override})
        deliverable = _s(args, "deliverable")
        d_note = ""
        if deliverable and deliverable.lower() != "none":
            known = {x["id"] for x in parse_deliverables(
                dev.read_general_doc(root, "project-prd") or "")}
            if deliverable not in known:
                return _err(f"Deliverable {deliverable!r} isn't in the project PRD "
                            f"(known: {', '.join(sorted(known)) or 'none'}). Propose a NEW "
                            "deliverable in prose for the owner to confirm — never record an "
                            "unconfirmed slug.")
            dev.set_work_item_scaffold(root, item_id, deliverable=deliverable)
            d_note = f" · deliverable `{deliverable}`"
        # Recorded in the same act as the kind, because it is the same judgment by the same
        # reader.
        try:
            dev.set_work_item_scale(root, item_id, _s(args, "scale") or "",
                                    _s(args, "scale_reason") or "")
            if (_fo := _s(args, "fanout")):
                dev.set_work_item_fanout(root, item_id, _fo)
        except ValueError as e:
            return _err(str(e))
        # Required-when-research cannot live in the arg schema, which has no way to express a
        # conditional.
        fam_note = ""
        if fam:
            try:
                dev.set_work_item_research_kind(root, item_id, fam,
                                                _s(args, "research_kind_reason") or "")
            except ValueError as e:
                return _err(str(e))
            fam_note = f" · research kind `{fam}`"
        # Recording a classification IS triage having run; a bare inbox push never stamps it.
        dev.set_work_item_triaged(root, item_id)
        t_note = f" · renamed to \"{title}\"" if renamed else ""
        return _ok(f"Recorded triage classification: kind `{kind}` · scale "
                   f"`{_s(args, 'scale')}`{fam_note}{d_note}{t_note}. The owner confirms at the "
                   "triage-exit "
                   "gate (Approve → plan); until then it stays a proposal on the item's own fields.")
    return set_triage_classification


class WriteCheckpointArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    working_on: Required[Annotated[str, "what is being worked on right now"]]
    decisions: Annotated[str, "decisions made, with the tradeoffs and leanings a transcript loses"]
    remaining: Required[Annotated[str, "what remains, concretely; the next session starts here"]]
    notes: Annotated[str, "tried-but-failed, gotchas, anything else worth carrying"]


def _write_checkpoint(*, store, context_id, dev_root=None, repo_dir=None, bound_item_id=None, **_):
    async def write_checkpoint(args: dict) -> dict:
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        # Derived from the item's phase, never asked of the agent, which has no reason to know its
        # session role.
        role = None
        try:
            from ....core import dev_knowledge as _dk  # noqa: F401
            from ....core.vocab import kind_profiles as _kp  # noqa: F401
            from ....daemon import app_state as _app
            it = _app.dev.read_work_item(dev_root, item_id) or {}
            role = _kp.session_slot(str(it.get("phase") or "triage"))
        except Exception:
            pass
        try:
            p = _arts.write_checkpoint(d, repo_dir, working_on=_s(args, "working_on"),
                                       decisions=_s(args, "decisions"),
                                       remaining=_s(args, "remaining"), notes=_s(args, "notes"),
                                       role=role)
        except ValueError as err:
            return _err(str(err))
        return _ok(f"Checkpoint banked: {p} (append-only; the next session's cold start reads the "
                   f"newest one). Reference artifacts by path — never duplicate their content here.")
    return write_checkpoint


class SyncFromAnchorBranchArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, ((((("the work-item id. It must have a live worktree, so this "
                                          "is build only")))))]]


def _sync_from_anchor_branch(*, store, context_id, dev_root=None, main_repo_dir=None, bound_item_id=None,
                    spine=None, **_):
    async def sync_from_anchor_branch(args: dict) -> dict:
        from pathlib import Path
        from ....core import git_layer as _gl
        from ....core.dev_knowledge import parse_md
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        meta, _body = parse_md((d / "item.md").read_text(encoding="utf-8"))
        wt = meta.get("git_worktree")
        if not wt or not Path(str(wt)).is_dir():
            return _err("This item has no live worktree — sync applies only during build "
                        "(the worktree is created at build entry).")
        try:
            # Sync from the repo's anchor: one anchored on `develop` must not pull `main` into an
            # item branch.
            rc = spine.repo(context_id) if spine else None
            res = _gl.sync_from_main(main_repo_dir or Path(str(wt)), Path(str(wt)),
                                     target=rc.anchor_branch if rc else None)
        except (_gl.GitError, _gl.GitBusy) as e:
            return _err(str(e))
        if res.get("up_to_date"):
            return _ok("Already up to date with the trunk — nothing to merge.")
        if res.get("conflicts"):
            return _err("Sync hit conflicts (merge aborted, your tree is unchanged): "
                        + ", ".join(res["conflicts"]) +
                        ". Commit your work, then resolve by re-running the merge yourself in the "
                        "worktree (`git merge <trunk>`, fix markers, commit) — or report the "
                        "conflict for the owner's Resolve-with-Agent action.")
        return _ok(f"Trunk merged into the item branch at {res['commit'][:10]}. Re-run your "
                   f"validation checks — evidence recorded before this merge is now stale.")
    return sync_from_anchor_branch
