"""Reading what an item has already decided, and asking the owner for authority."""

from typing import Annotated, Literal, Required, TypedDict

from .render import _err, _ok, _s
from .items import _bound_err, _item_dir

class ReadDecisionsArgs(TypedDict, total=False):
    entry_id: Annotated[str, ((((("a `D-NNN` id to read in full. Omit it for the index of every "
                                  "decision's id, status and title")))))]


def _read_decisions(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def read_decisions(args: dict) -> dict:
        from ....core import decision_ledger as _ledger
        entry_id = _s(args, "entry_id")
        if not entry_id:
            return _ok("## Decisions already settled in this project\n"
                       + _ledger.settled_index(dev_root)
                       + "\n\nA title that answers your question means it is ALREADY RULED — cite "
                         "the id as the answer instead of asking the owner again. Pass `entry_id` "
                         "for the full entry when a title looks close but you need the wording.")
        for e in _ledger.read_entries(dev_root):
            if e["id"].lower() == entry_id.lower():
                return _ok(f"### {e['id']} · {e['title']} · {e['status']}\n{e['body']}")
        return _err(f"No decision {entry_id!r} in this project's ledger.")
    return read_decisions


class ReadResearchProposalsArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the research work-item id whose review record to read"]]


def _read_research_proposals(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def read_research_proposals(args: dict) -> dict:
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if err := _bound_err(item_id, bound_item_id):
            return _err(err)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} in this repo — check the id you were given.")
        props = _arts.research_proposals(d)
        if not props:
            return _ok("This review proposes no work. That is a real outcome — report "
                       "`clean_noop` and say so in the Owner's decision line.")
        filed, held = _arts.filed_and_withheld(props)
        # A ruling that changed nothing is an answer, not work, so it is reported and never filed.
        filed, settled = _arts.filed_and_settled(props)
        out = [f"## File these ({len(filed)})"]
        for p in filed:
            out.append(f"\n### {p['title']}")
            out.append(f"- work_kind: {p['kind'] or '(untyped — the report owed one)'}")
            for label, key in (("why now", "why_now"), ("delivers", "delivers"),
                               ("default applied", "default_applied"),
                               ("owner's ruling", "answer")):
                if p.get(key):
                    out.append(f"- {label}: {p[key]}")
        if not filed:
            out.append("- (none)")
        # Naming the withheld keeps a half-filed review distinguishable from a clean one — an
        # absence is invisible.
        out.append(f"\n## Do NOT file these ({len(held)}) — the owner has not ruled")
        for p in held:
            out.append(f"\n### {p['title']}")
            out.append(f"- question: {p['question']}")
            out.append(f"- reserved because: {p['reserved_because'] or '(unstated)'}")
        if not held:
            out.append("- (none)")
        out.append(f"\n## Do NOT file these ({len(settled)}) — settled, nothing to do")
        for p in settled:
            out.append(f"\n### {p['title']}")
            out.append(f"- question: {p['question'] or '(none)'}")
            out.append(f"- owner's ruling: {p['answer']}")
            out.append("- the ruling left nothing to build. Name it under `## Settled` in the "
                       "review record so the answer is on the record, and file NO item.")
        if not settled:
            out.append("- (none)")
        if issues := _arts.research_proposal_issues(props):
            out.append("\n## Malformed proposal blocks")
            out.extend(f"- {i}" for i in issues)
        return _ok("\n".join(out))
    return read_research_proposals


class RequestAuthorizationArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    what: Required[Annotated[str, "the contract change you can't self-authorize, one line "
                                  "(e.g. 'retire the legacy spec.md doc')"]]
    why: Required[Annotated[str, "one line: why it's needed and why it's above your pay grade"]]
    doc: Required[Annotated[str, "the anchor doc it touches (project-prd|architecture|capabilities|"
                                 "decisions|roadmap|resources|spec)"]]
    scope: Required[Annotated[
        Literal["doc-sync", "rename-to-shipped", "roadmap-mark-done",
                "prd-identity", "roadmap-scope", "new-decision", "doc-delete"],
        ((((("matched against the deputy's delegated authority. Delegable: `doc-sync` · "
             "`rename-to-shipped` · `roadmap-mark-done`. Owner-reserved: `prd-identity` · "
             "`roadmap-scope` · `new-decision` · `doc-delete`")))))]]
    check: Annotated[str, ((((("the vet-plan check id this blocks, so that check defers instead of "
                               "failing")))))]


def _request_authorization(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def request_authorization(args: dict) -> dict:
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        item = {}
        try:
            from ....core.dev_knowledge import DevKnowledgeService as _DK
            item = _DK().read_work_item(dev_root, item_id) or {}
        except Exception:
            pass
        # Checked against the staged ops: a delegable claim over ops that change intent must not
        # route past the owner.
        try:
            from ....core import knowledge_delta as _kd
            staged = (_kd.read_delta(d) or {}).get("ops") or []
        except Exception:
            staged = []
        if (mismatch := _arts.scope_mismatch(_s(args, "scope"), staged)):
            return _err(f"Authorization refused — {mismatch}")
        try:
            a = _arts.record_authorization(
                d, what=_s(args, "what"), why=_s(args, "why"), doc=_s(args, "doc"),
                scope=_s(args, "scope"), check=_s(args, "check"),
                phase=str(item.get("phase") or ""))
        except ValueError as err:
            return _err(str(err))
        return _ok(f"Authorization requested: {a['what']} (scope {a['scope']}). The blocked vet "
                   f"check now DEFERS — do NOT edit the vet plan and do NOT force the change through. "
                   f"Complete everything else and report. The request rides to REVIEW, where the "
                   f"owner (or a delegated deputy) grants or denies; a grant routes back to you to "
                   f"perform it, a denial accepts the gap on the record.")
    return request_authorization
