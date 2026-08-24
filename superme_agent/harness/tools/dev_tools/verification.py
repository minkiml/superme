"""Recording evidence: a validation run, a verification, a diagnosis, a lens."""

import asyncio
from typing import Annotated, Literal, Required, TypedDict

from .render import _err, _ok, _s
from .items import _bound_err, _item_dir

class DryRunChecksArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]


def _dry_run_checks(*, store, context_id, dev_root=None, repo_dir=None, bound_item_id=None, **_):
    async def dry_run_checks(args: dict) -> dict:
        """Smoke-test the `run:` blocks already written into this item's plan. Records nothing."""
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        if repo_dir is None:
            return _err("no repo to run in from this session.")
        from ....daemon.services import checks as _checks
        try:
            rows = await asyncio.to_thread(_checks.dry_run, d, repo_dir)
        except Exception as e:                       # a dry run must never take the turn down
            return _err(f"dry run could not complete: {e}")
        if not rows:
            return _ok("nothing to dry-run — no check in this plan carries a `run:` block "
                       "(or this host has no sandbox).")
        return _ok("\n".join(f"{r['check']}: {r['result']}" for r in rows)
                   + "\n\nA failing assertion is EXPECTED — the work is not built yet. What you "
                     "are looking for is a command that could not run at all (usage error, import "
                     "error, wrong path): that one will never come back green, whatever build does.")
    return dry_run_checks


class RecordValidationArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    command: Required[Annotated[str, ("the command you ran, verbatim and re-runnable from the "
                                      "worktree root — vet re-executes this exact string to audit "
                                      "the claim, so a paraphrase makes the record uncheckable")]]
    result: Required[Annotated[str, "the MACHINE result — exit code, counts, output tail"]]
    passed: Required[Annotated[bool, "did it pass"]]
    task: Annotated[str, "the plan task id this run defends, when it defends exactly one (`t3`)"]


def _record_validation(*, store, context_id, dev_root=None, repo_dir=None,
                       bound_item_id=None, **_):
    async def record_validation(args: dict) -> dict:
        """Build's own self-check, recorded as data instead of prose. Vet re-runs each command and
        compares, so an unearned green returns as a finding rather than reaching the owner."""
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            e = _arts.record_validation(d, repo_dir, command=_s(args, "command") or "",
                                        result=_s(args, "result") or "",
                                        passed=bool(args.get("passed")),
                                        task=_s(args, "task") or "")
        except (ValueError, OSError) as ex:
            return _err(str(ex))
        return _ok(f"recorded — `{e['command']}` {'passed' if e['passed'] else 'FAILED'} "
                   f"(cycle {e['cycle']}). Vet re-runs this exact command to audit the claim.")
    return record_validation


class RecordVerificationArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    check: Required[Annotated[str, "the verification-plan check id, verbatim (it keys the ledger)"]]
    how: Required[Annotated[str, "the exact command / procedure that ran"]]
    result: Required[Annotated[str, "the MACHINE result — exit code, counts, output tail"]]
    passed: Required[Annotated[bool, "did the check pass (false when deferred)"]]
    note: Annotated[str, "for a failure: expected vs actual in one line"]
    deferred: Annotated[bool, ("set true for a check the BUILD deferred to the owner (a needs-you "
                               "item pending authorization at review): it is recorded as an "
                               "intentional skip, not a failure — you did NOT run it")]
    met: Annotated[list[str], ("rubric checks only: the plan's criteria this build MEETS, verbatim. "
                               "Every criterion the plan lists must appear in met or missed")]
    missed: Annotated[list[str], ("rubric checks only: the criteria it does NOT meet. Any missed "
                                  "criterion means passed=false — a rubric is the bar, not a score")]


def _record_verification(*, store, context_id, dev_root=None, repo_dir=None,
                         bound_item_id=None, **_):
    async def record_verification(args: dict) -> dict:
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            e = _arts.record_verification(d, repo_dir, check=_s(args, "check"),
                                          how=_s(args, "how"), result=_s(args, "result"),
                                          passed=bool(args.get("passed")),
                                          deferred=bool(args.get("deferred")),
                                          note=_s(args, "note"),
                                          met=args.get("met") or [],
                                          missed=args.get("missed") or [])
        except ValueError as err:
            return _err(str(err))
        verdict = _arts.evidence_status(d, repo_dir)
        return _ok(f"Recorded: {e['check']} · passed={e['passed']} · cycle {e['cycle']} · "
                   f"fingerprint={e['fingerprint']}. Derived verdict now: {verdict['status']} "
                   f"({verdict['entries']} entries). Evidence goes STALE on any further repo edit — "
                   f"re-run checks after changes; never claim verified without a fresh green record.")
    return record_verification


class RecordDiagnosisArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    check: Required[Annotated[str, "the FAILING check's plan id, verbatim"]]
    where: Required[Annotated[str, ("the narrowest located source — file:line, the failing frame, "
                                    "the request that errored. Not 'in the parser' when you know "
                                    "which line")]]
    why: Required[Annotated[str, ("the mechanism, as far as the evidence supports it — what "
                                  "actually happens, not what should have")]]
    unknown: Annotated[str, ("what you could NOT determine, if anything. An honest gap beats a "
                             "confident guess: it tells the next build cycle where you did not "
                             "look")]


def _record_diagnosis(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def record_diagnosis(args: dict) -> dict:
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            r = _arts.record_diagnosis(d, check=_s(args, "check"), where=_s(args, "where"),
                                       why=_s(args, "why"), unknown=_s(args, "unknown"))
        except ValueError as err:
            return _err(str(err))
        return _ok(f"Diagnosis recorded for {r['check']} (cycle {r['cycle']}). It reaches the next "
                   "build cycle as its work order — do not add the fix; build reasons that out "
                   "inside the current plan.")
    return record_diagnosis


class LensFindingArg(TypedDict, total=False):
    severity: Required[Annotated[Literal["low", "medium", "high"],
                                 "severity decides whether this gates the cycle"]]
    text: Required[Annotated[str, "the finding in one line, naming where it is"]]


class RecordLensArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    lens: Required[Annotated[Literal["intent", "safety", "robustness", "performance"],
                             "which standing lens this entry records"]]
    probed: Required[Annotated[list[str], ("what you examined or tried through this lens, ONE "
                                           "PROBE PER ENTRY — an input you tried, a path you read, "
                                           "a command you ran, each with its outcome. Not a "
                                           "paragraph: the owner reads this list to see what was "
                                           "actually checked, and it is what makes a clean pass a "
                                           "real answer")]]
    findings: Annotated[list[LensFindingArg],
                        ("what the lens found. Empty is expected and correct when there is "
                         "nothing — never manufacture one to fill the list")]


def _record_lens(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def record_lens(args: dict) -> dict:
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            r = _arts.record_lens(d, lens=_s(args, "lens"),
                                  probed=args.get("probed") or [],
                                  findings=args.get("findings") or [])
        except ValueError as err:
            return _err(str(err))
        n = len(r["findings"])
        return _ok(f"{r['lens']} lens recorded (cycle {r['cycle']}): "
                   + (f"{n} finding(s)" if n else "nothing found")
                   + (" — this gates, so the item goes back to build" if r["gates"] else ""))
    return record_lens


class NominateCheckArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    check: Required[Annotated[str, "the verification-plan check id to nominate"]]
    general: Required[Annotated[str, ("what makes it general — the property of THIS REPO it "
                                      "defends, said without mentioning this item. If you cannot "
                                      "say it that way, it isn't a library entry")]]


def _nominate_check(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def nominate_check(args: dict) -> dict:
        from ....core import artifacts as _arts
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        try:
            r = _arts.record_nomination(d, check=_s(args, "check"), general=_s(args, "general"))
        except ValueError as err:
            return _err(str(err))
        return _ok(f"{r['check']} nominated for this repo's verification library. Close writes it "
                   "in as an AVAILABLE entry — the owner decides whether it becomes standing.")
    return nominate_check


class ReadVerificationLibraryArgs(TypedDict, total=False):
    item_id: Annotated[str, "the work-item id — include it at close to also get this item's "
                            "nominations rendered as ready-to-write entries"]


def _read_verification_library(*, store, context_id, dev_root=None, bound_item_id=None, **_):
    async def read_verification_library(args: dict) -> dict:
        from pathlib import Path
        from ....core import artifacts as _arts
        from ....core import verification_library as _vl
        lib = _vl.read_library(Path(dev_root))
        out = []
        for tier in _vl.TIERS:
            rows = lib[tier]
            out.append(f"## {tier} ({len(rows)})")
            out.extend(f"- `{e['id']}` — {e['proves'] or e['scenario'] or e['traces']}"
                       for e in rows)
            if not rows:
                out.append("- (none)")
        item_id = _s(args, "item_id")
        d = _item_dir(dev_root, item_id) if item_id else None
        if d is not None and not _bound_err(item_id, bound_item_id):
            noms = _arts.nominations(d)
            checks = {c["id"]: c for c in _arts.parse_vet_plan(
                (d / "artifacts" / _arts.artifact_file("plan")).read_text(encoding="utf-8")).get("checks", [])}
            blocks = [_vl.render_entry(checks[cid]) for cid in noms if cid in checks]
            if blocks:
                out.append("\n## nominated by this item — write each as an `append` op on "
                           f"doc `{_vl.LIBRARY_DOC}`, section `Available`")
                out.extend(f"\n```\n{b.rstrip()}\n```" for b in blocks)
        return _ok("\n".join(out))
    return read_verification_library
