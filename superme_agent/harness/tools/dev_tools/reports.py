"""The phase pens. One rule builds every path, so no phase names its own."""

from typing import Annotated, Required, TypedDict

from .render import _err, _ok, _s
from .items import _bound_err, _item_dir

class FilePlanReportArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    summary: Required[Annotated[str, ("one line, plain words — what is going to happen. The "
                                      "dashboard shows this line on its own, so it must stand "
                                      "without the rest of the report")]]
    approach: Annotated[str, ("the plan in the owner's terms, bullets — implementation: what "
                              "changes and what deliberately does not; research: what we are "
                              "trying to find out. A small ASCII flow in a fence when it carries "
                              "the meaning better than words")]
    confirm: Annotated[str, ("implementation: what the checks will NOT tell you, one short "
                             "paragraph under the confirmation table (the table itself is "
                             "derived); research: how we will look and what we will not, since a "
                             "research item has no table")]
    decisions: Annotated[str, ("one per line, each tagged (Owner) or (Agent), from plan.md "
                               "## Decisions & clarifications. Omit if none were made")]
    assumptions: Annotated[str, ("one per line, each tagged (Owner) or (Agent) — what this plan "
                                 "takes for granted and would have to revisit. Omit if none")]


def _file_plan_report(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def file_plan_report(args: dict) -> dict:
        """The plan gate's user report. The confirmation table is DERIVED from the verification plan's
        checks — each row is a check's `proves:` line and how it will be run — and so are the stats."""
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
            meta, _b = parse_md((d / "item.md").read_text())
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
    body: Required[Annotated[str, ("the whole report, filled from this phase's template in "
                                   "`templates/` — every section, every `<fill:…>` slot replaced. "
                                   "Passed verbatim; nothing is derived and nothing is appended")]]


FileInvestigateReportArgs = FilePhaseReportArgs


def _phase_report_pen(phase: str):
    """Build the pen for one phase's user-facing report.

    ONE FACTORY, because there is one rule: `<item>/reports/report-<phase>.md`, built in code and
    never named to an agent — a phase naming its own path resolves it against whatever its cwd is."""
    def factory(*, store, context_id, dev_root=None, bound_item_id=None, **_):
        async def file_phase_report(args: dict) -> dict:
            from ....core import artifacts as _arts
            item_id = _s(args, "item_id")
            if (msg := _bound_err(item_id, bound_item_id)):
                return _err(msg)
            d = _item_dir(dev_root, item_id)
            if d is None:
                return _err(f"No work-item {item_id!r} here.")
            body = _s(args, "body") or ""
            if not body.strip():
                return _err("body is empty — the report is the deliverable, not a formality.")
            stem = f"report-{phase}"
            path = d / "reports" / f"{stem}.md"
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body if body.endswith("\n") else body + "\n")
            except OSError as e:
                return _err(str(e))
            if issues := _arts.report_issues(d, stem):
                return _err(f"{path} written, but it is not finished: {'; '.join(issues)}")
            return _ok(f"{path} written.")
        file_phase_report.__name__ = f"file_{phase}_report"
        return file_phase_report
    return factory


_file_triage_report = _phase_report_pen("triage")
_file_build_report = _phase_report_pen("build")
_file_review_report = _phase_report_pen("review")
_file_close_report = _phase_report_pen("close")


def _file_investigate_report(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def file_investigate_report(args: dict) -> dict:
        """The investigation's user report. The path is built in code, and an unfilled slot is refused
        rather than shipped."""
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        body = _s(args, "body") or ""
        if not body.strip():
            return _err("body is empty — the report is the deliverable, not a formality.")
        path = d / "reports" / "report-investigate.md"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body if body.endswith("\n") else body + "\n")
        except OSError as e:
            return _err(str(e))
        if issues := _arts.report_issues(d, "report-investigate"):
            return _err(f"{path} written, but it is not finished: {'; '.join(issues)}")
        return _ok(f"{path} written.")
    return file_investigate_report


class FileVetReportArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    summary: Required[Annotated[str, ("one line — what this pass establishes, in the owner's "
                                      "terms. The dashboard shows it alone, so it must stand "
                                      "without the report around it")]]
    confirms: Required[Annotated[str, ("`## What this confirms` — a bullet per thing that is now "
                                       "known to be true, in the product's words, not the check's. "
                                       "Do NOT re-list the checks: the Task tab carries them, and "
                                       "a second list makes your independent pass read like "
                                       "build's self-report")]]
    looked_at: Required[Annotated[str, ("`## What else was looked at` — the lenses in plain "
                                        "language: what question you asked, what you probed, and "
                                        "what came of it. A lens that found nothing still earns "
                                        "its bullet; what you probed is the evidence the question "
                                        "was asked")]]
    unknown: Annotated[str, ("`## What I can't tell you` — one line and a short reason, for what "
                             "this pass could not settle and why. Omit only when there is "
                             "genuinely nothing, which is rare")]


def _file_vet_report(*, store, context_id, dev_root=None, repo_dir=None, bound_item_id=None, **_):
    async def file_vet_report(args: dict) -> dict:
        """The vet cycle's user report — HYBRID: you write the narrative, code writes
        `## What didn't hold` off the recorded entries, so a failure reaches the owner whatever the
        prose says."""
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
