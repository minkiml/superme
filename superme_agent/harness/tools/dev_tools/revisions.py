"""Typed edits to a durable record: the knowledge delta, and a plan revision."""

import json
from typing import Annotated, Literal, Required, TypedDict

from .render import _err, _ok, _s
from .items import _bound_err, _item_dir

class KnowledgeOpArg(TypedDict):
    doc: Annotated[Literal["project-prd", "architecture", "capabilities", "decisions",
                           "roadmap", "resources", "verification"],
                   "which anchor doc this op edits (the retired `spec` is read-only — its "
                   "content lives in architecture/decisions now). `verification` is close's "
                   "library write and nothing else's: append a vet nomination under `Available`, "
                   "never under `Standing` (promoting is the owner's call alone)"]
    section: Annotated[str, "the exact `## heading` text the op targets (must exist in the doc)"]
    op: Annotated[Literal["update", "append", "supersede", "rename_section"],
                  "update/supersede replace the section body; append extends it; rename_section "
                  "rewrites the `## heading` LINE itself (content = the new heading text)"]
    content: Annotated[str, ("for body ops: the section BODY markdown only — do NOT repeat the "
                             "`## <section>` heading (the writer keeps it; a repeated heading is "
                             "stripped). For rename_section: the new heading TEXT, one line, no `##`")]


class ApplyKnowledgeDeltaArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    ops: Required[Annotated[list[KnowledgeOpArg],
                            ("the edit ops — validated, then written to the anchor docs and "
                             "recorded in this week's change log. A rejection writes nothing")]]


def _apply_knowledge_delta(*, store, context_id, dev_root=None, repo_dir=None,
                           bound_item_id=None, **_):
    async def apply_knowledge_delta(args: dict) -> dict:
        from pathlib import Path
        from ....core import knowledge_delta as _kd
        from ....core import verification_library as _vl
        from ....core.dev_knowledge import DevKnowledgeService as _DK
        from ....core.vocab.kind_profiles import get_profile, is_final_phase
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        item = _DK().read_work_item(Path(dev_root), item_id) or {}
        if not get_profile(item.get("kind")).knowledge_writes:
            return _err("This item's kind never writes general dev-knowledge (D7). The anchor "
                        "docs describe what is IN the main tree, so they change only when code "
                        "does. Nothing this item concludes has been implemented yet — its "
                        "conclusions belong in its own report, and reach the docs later via the "
                        "work that acts on them.\n"
                        "One anchor doc is not covered by that reason and is written anyway: "
                        "`decisions.md` is immutable HISTORY, not current-state truth, so an "
                        "owner's ruling belongs in it the moment they give it. THE KERNEL writes "
                        "that entry at the review gate, from the typed proposal the owner answered "
                        "— never an agent, so the ledger's every entry traces to a question an "
                        "owner was asked. You have nothing to do there; it is already recorded.")
        # Before close the owner has not locked the code, so a doc written earlier could describe
        # something that never lands.
        if not is_final_phase(item.get("kind"), item.get("phase") or "triage"):
            return _err(f"The anchor docs are written at CLOSE — this item is at "
                        f"`{item.get('phase')}`. Until the merge locks the code there is nothing "
                        f"true to write about it yet.")
        # A blocking child's content is not on main until the parent lands, so the parent's close
        # speaks for the family.
        sf = item.get("spawned_from") or {}
        if isinstance(sf, dict) and sf.get("relation") == "blocking":
            return _err(f"This is a blocking child of `{sf.get('item')}` — its work landed on the "
                        f"parent's branch, not on main, so the anchor docs cannot describe it yet. "
                        f"The parent's close writes for the family; note what it should say in "
                        f"your close report.")
        ops = args.get("ops")
        if isinstance(ops, str):
            try:
                ops = json.loads(ops) if ops.strip() else None
            except (ValueError, TypeError) as e:
                return _err(f"`ops` must be a JSON array of edit ops: {e}")
        # The library doc appears the first time a close has something to put in it, so validation
        # allows its absence.
        if any(isinstance(o, dict) and o.get("doc") == _vl.LIBRARY_DOC for o in (ops or [])):
            _vl.seed(Path(dev_root))
        issues = _kd.validate_ops(ops, Path(dev_root), repo_dir)
        if issues:
            return _err("Delta rejected — NOTHING was written. Fix and re-apply:\n- "
                        + "\n- ".join(issues))
        try:
            res = _kd.apply_ops(Path(dev_root), ops)
        except (ValueError, OSError) as e:
            return _err(f"Write failed, docs unchanged: {e}")
        log_path = _kd.append_change_log(Path(dev_root), item_id,
                                         str(item.get("title") or ""), ops)
        store.log_event(context_id, "knowledge.applied",
                        f"Anchor docs updated at close ({res['applied']} op(s) → "
                        f"{', '.join(res['docs'])})",
                        item_id=item_id, actor="agent",
                        meta={**res, "change_log": log_path})
        return _ok(f"Written: {res['applied']} op(s) → {', '.join(res['docs'])}, and this week's "
                   f"change log has the entry. Say in your close report what the docs now claim.")
    return apply_knowledge_delta


class PlanOpArg(TypedDict, total=False):
    op: Required[Annotated[Literal["update", "append", "add_task", "edit_task", "remove_task"],
                           "update/append act on a section BODY; the *_task ops act on ONE task "
                           "line in `## Tasks` (its `- [x]` state survives an edit)"]]
    section: Annotated[str, "section ops only: the exact `## heading` text (must already exist)"]
    task: Annotated[str, "edit_task/remove_task only: the task id, e.g. `t3`"]
    content: Annotated[str, ("the new text — a section BODY (no `## heading`) for section ops, the "
                             "task text (no `- [ ] t<n> —` prefix) for add_task/edit_task; omit "
                             "for remove_task")]


class PlanChangeArg(TypedDict, total=False):
    area: Required[Annotated[str, ("what this change answers, in a few words (`caching design`, "
                                   "`cli tasks`) — a concern with no change is a dropped concern, "
                                   "and this is what makes that visible")]]
    scope: Required[Annotated[Literal["resume", "targeted", "redesign"],
                              "resume = the plan was right; run another generation against it "
                              "unchanged (NO ops — an edit here is refused) · targeted = right in "
                              "approach, wrong in places · redesign = the approach itself was "
                              "wrong; rewrite it and remove the void tasks explicitly"]]
    note: Required[Annotated[str, "one line: what changed here, or why nothing needed to"]]
    ops: Annotated[list[PlanOpArg], ("this change's edits — required except at `resume`, where "
                                     "they are refused")]
    superseded: Annotated[str, ("redesign only: what prior work is void and what build must undo "
                                "(forward, with new commits — never a reset)")]


class RevisePlanArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    feedback: Required[Annotated[str, ("the feedback driving this revision, VERBATIM — the "
                                       "owner's (or deputy's) words, never your paraphrase")]]
    directive: Required[Annotated[str, ("what the next build does DIFFERENTLY because of this "
                                        "revision — the one line it acts on")]]
    still_in_force: Required[Annotated[str, ("what earlier revisions still bind (`nothing` on the "
                                             "first). Build reads the newest block; this is what "
                                             "makes that honest")]]
    changes: Required[Annotated[list[PlanChangeArg],
                                "one entry per concern, each with its OWN scope — validated "
                                "first; a refusal writes nothing"]]


def _revise_plan(*, store, context_id, dev_root=None, bound_item_id=None, spine=None, **_):
    async def revise_plan(args: dict) -> dict:
        from ....core import plan_revision as _pr
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        path = _pr.plan_path(d)
        if not path.is_file():
            return _err("This item has no plan.md yet — scaffold and author it first "
                        "(`scaffold_artifact`); a revision edits a plan that already exists.")
        # plan.md belongs to the plan phase; review is read-only on it, which is exactly why
        # review has `revise`.
        from ....core.dev_knowledge import parse_md
        phase = str((parse_md((d / "item.md").read_text())[0] or {}).get("phase") or "")
        if phase != "plan":
            return _err(
                f"plan.md is the PLAN phase's to write — this item is at `{phase}`. "
                f"If the conversation concluded the plan must change, end your run with "
                f"`report_completion(machine.outcome='revise')` and carry the owner's words in "
                f"`machine.summary`: the item flips to plan in this same thread and the plan turn "
                f"makes the edit, so build and vet re-run against what changed. Editing it here "
                f"would change the contract with nothing downstream re-running against it.")
        feedback = (_s(args, "feedback") or "").strip()
        if not feedback:
            return _err("`feedback` is required — the words that drove this revision are what the "
                        "next build reads first.")
        directive = (_s(args, "directive") or "").strip()
        if not directive:
            return _err("`directive` is required — one line on what the next build does "
                        "DIFFERENTLY. Without it the block records a complaint, not an instruction.")
        still = (_s(args, "still_in_force") or "").strip()
        if not still:
            return _err("`still_in_force` is required — what earlier revisions still bind (say "
                        "`nothing` on the first). Build reads the newest block; this is what keeps "
                        "that honest.")
        changes = args.get("changes")
        if isinstance(changes, str):   # tolerate a JSON string (older skill text / tests)
            try:
                changes = json.loads(changes) if changes.strip() else None
            except (ValueError, TypeError) as e:
                return _err(f"`changes` must be a JSON array of changes: {e}")
        issues = _pr.validate(path.read_text(), changes)
        if issues:
            return _err("Revision rejected — plan.md is unchanged. Fix and re-send:\n- "
                        + "\n- ".join(issues))
        # Both come from the record, never the agent: they are what start the next generation's
        # budget at zero.
        concerns = _pr.derive_concerns(d)
        spend = spine.item_phase_tokens(context_id, item_id) if spine else 0
        res = _pr.revise(d, changes=changes, feedback=feedback, directive=directive,
                         still_in_force=still, concerns=concerns, spend=spend)
        scopes = sorted({str(c.get("scope") or "") for c in changes})
        store.log_event(context_id, "plan.revised",
                        f"plan.md revised ({res['revision']}, {'/'.join(scopes)}): {feedback[:160]}",
                        item_id=item_id, actor="agent",
                        meta={"revision": res["revision"], "scopes": scopes,
                              "concerns": concerns, "changed": res["changed"]})
        return _ok(f"plan.md revised as {res['revision']} ({res['ops']} op(s)): "
                   + "; ".join(res["changed"])
                   + f". Concerns on record: {', '.join(concerns)} (read off the loop's exit and "
                     f"the authorization ledger — not yours to assert). Every section you didn't "
                     f"name is untouched, `## Tasks` and `## Verification plan` stay last as the "
                     f"live truth, and the block sits above them. This opens a new generation: the "
                     f"loop's budget and recurrence guards restart from here.")
    return revise_plan
