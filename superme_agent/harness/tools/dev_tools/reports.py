"""The phase pens. One rule builds every path, so no phase names its own."""

import re
from typing import Annotated, Required, TypedDict

from .render import _err, _ok, _s
from .items import _bound_err, _item_dir

class FilePlanReportArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    summary: Required[Annotated[str, (((((("one line in plain words: what is going to happen. The "
                                           "dashboard shows it alone, so it must stand without the "
                                           "report"))))))]]
    approach: Annotated[str, (((((("the plan in the owner's terms, as bullets. Implementation: what "
                                   "changes and what deliberately does not. Research: what we are "
                                   "trying to find out"))))))]
    confirm: Annotated[str, (((((("implementation: what the checks will not tell you, one short "
                                  "paragraph. Research: how we will look, and how we will not"))))))]
    decisions: Annotated[str, (((((("one per line, tagged (Owner) or (Agent), from the plan's "
                                    "decisions section. Omit when none were made"))))))]
    assumptions: Annotated[str, (((((("one per line, tagged (Owner) or (Agent): what the plan takes "
                                      "for granted and would revisit. Omit when none"))))))]


def _file_plan_report(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def file_plan_report(args: dict) -> dict:
        """The plan gate's user report.

        The confirmation table is DERIVED from the verification plan's checks, and so are the stats."""
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        kind = None
        try:
            from ....core.dev_knowledge import parse_md
            meta, _b = parse_md((d / "item.md").read_text(encoding="utf-8"))
            kind = meta.get("kind")
        except Exception:
            pass
        try:
            r = _arts.write_plan_user_report(
                d, summary=_s(args, "summary"), approach=_s(args, "approach"),
                confirm=_s(args, "confirm"), decisions=_s(args, "decisions"),
                assumptions=_s(args, "assumptions"), item_kind=kind)
        except (ValueError, OSError) as e:
            return _err(str(e))
        gaps = r["uncovered"]
        return _ok(f"{r['path']} written — {r['tasks']} task(s), {r['checks']} check(s)."
                   + (f" {len(gaps)} task(s) have NO check ({', '.join(gaps)}): either add one or "
                      "be ready to say at the gate why that task needs no proof."
                      if gaps else " Every task is defended."))
    return file_plan_report


class FilePhaseReportArgs(TypedDict, total=False):
    """The argument shape every whole-body pen shares: the item, and the finished report."""
    item_id: Required[Annotated[str, "the work-item id"]]
    body: Required[Annotated[str, (((((("the whole report, filled from this phase's template with "
                                        "every `<fill:…>` slot replaced. It is written verbatim; "
                                        "nothing is derived or appended"))))))]]


# Phases whose report is one whole body, handed over as written. plan and vet derive part of
# theirs.
_WHOLE_BODY_PHASES = ("triage", "build", "review", "close", "investigate")


def _file_phase_report(*, store, context_id, dev_root=None, bound_item_id=None, scope=None, **_):
    """The pen for whichever phase's report this session is entitled to write.

    The phase comes from the mounted scope, never the agent, which could name another's."""
    async def file_phase_report(args: dict) -> dict:
        from ....core import artifacts as _arts
        phase = str(scope or "")
        if phase not in _WHOLE_BODY_PHASES:
            return _err(f"This session ({phase or 'no phase'}) has no whole-body report to file.")
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        body = _s(args, "body") or ""
        if not body.strip():
            return _err("body is empty — the report is the deliverable, not a formality.")
        # A body copied out of a fenced template can arrive with an escape before its heading, and
        # the owner reads this file. Only blank and lone-backslash lines are dropped.
        body = re.sub(r"\A(?:[ \t]*\\?[ \t]*\r?\n)+(?=#)", "", body)
        stem = f"report-{phase}"
        path = d / "reports" / f"{stem}.md"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8")
        except OSError as e:
            return _err(str(e))
        if issues := _arts.report_issues(d, stem):
            return _err(f"{path} written, but it is not finished: {'; '.join(issues)}")
        return _ok(f"{path} written.")
    return file_phase_report


class FileVetReportArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    summary: Required[Annotated[str, (((((("one line: what this pass establishes, in the owner's "
                                           "terms. The dashboard shows it alone, so it must stand "
                                           "by itself"))))))]]
    confirms: Required[Annotated[str, (((((("a bullet per thing now known to be true, in the "
                                            "product's words not the check's. Do not re-list the "
                                            "checks the Task tab has"))))))]]
    looked_at: Required[Annotated[str, (((((("the lenses in plain language: the question you asked, "
                                             "what you probed, what came of it. A lens that found "
                                             "nothing still earns its bullet"))))))]]
    unknown: Annotated[str, (((((("one line on what this pass could not settle, in plain words "
                                  "with no code names. Omit only when genuinely nothing"))))))]


def _file_vet_report(*, store, context_id, dev_root=None, repo_dir=None, bound_item_id=None, **_):
    async def file_vet_report(args: dict) -> dict:
        """The vet cycle's user report. Hybrid: you write the narrative.

        Code writes `## What didn't hold` off the recorded entries, so a failure reaches the owner
        regardless."""
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            r = _arts.write_vet_user_report(
                d, repo_dir, summary=_s(args, "summary") or "",
                confirms=_s(args, "confirms") or "", looked_at=_s(args, "looked_at") or "",
                unknown=_s(args, "unknown") or "")
        except ValueError as err:
            return _err(f"Vet report refused — {err}")
        store.log_event(context_id, "vet.report",
                        f"Vet report filed: verdict {r['verdict']}"
                        + (f" · failing: {', '.join(r['failed'])}" if r["failed"] else ""),
                        item_id=item_id, actor="agent",
                        meta={"verdict": r["verdict"], "failed": r["failed"]})
        return _ok(f"Vet report filed: {r['path']} (derived verdict: {r['verdict']}). This "
                   "session's job is done — do not attempt fixes; the loop hands failures back "
                   "to the build session.")
    return file_vet_report
