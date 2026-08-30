"""Recording evidence: a validation run, a verification, a diagnosis, a lens."""

import asyncio
from typing import Annotated, Literal, Required, TypedDict

from .render import _err, _ok, _s
from .items import _bound_err, _item_dir

class CheckPlanCommandsArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]


def _plan_coverage(item_dir, kind: str | None) -> str:
    """Which of the plan's tasks nothing will prove — the same count `file_plan_report` reports.

    It lived only there, so planning learned of a gap by FILING the report, then went back through
    authoring and dry-run and filed again: eight calls on one live run to add one check. Reading it
    here costs nothing, because the dry-run is already the last step before the report."""
    from ....core.artifacts import proof_rows
    from ....core.vocab.kind_profiles import get_profile
    if get_profile(kind).kind == "research":
        return ""   # a research plan declares no checks BY DESIGN — a gap call-out would be noise
    rows = [r for r in proof_rows(item_dir) if r["task"]]
    if not rows:
        return ""
    gaps = [r for r in rows if not r["verified"]]
    if not gaps:
        return f"\n\nAll {len(rows)} task(s) are defended by a check."
    named = ", ".join(f"{r['task']} ({r['text'][:60]})" for r in gaps)
    return (f"\n\n{len(gaps)} of {len(rows)} task(s) have NO check: {named}. Add one now, or be "
            "ready to say at the gate why that task needs no proof — the report you file next "
            "reports this same count to the owner.")


# Writing to a pipe or a temp file is not "leaving the worktree" — only naming another CHECKOUT is.
# A drive letter or a UNC share is absolute too. Matching only `/...` left the detector blind
# to every native Windows path.
_ABS_TOKEN = r"(?<![\w=])(?:[A-Za-z]:[\\/][^\s'\"|;&)]*|\\\\[^\s'\"|;&)]+|/[^\s'\"|;&)]+)"
_SYSTEM_ROOTS = ("/dev/", "/tmp/", "/var/folders/", "/private/tmp/", "/usr/", "/etc/", "/bin/",
                 "/sbin/", "/opt/", "/proc/")


def _stray_run_blocks(item_dir, repo_dir) -> str:
    """`run:` blocks that leave this item's worktree — the one failure the dry run cannot see.

    A block that `cd`s to the primary checkout RUNS THERE happily: the tree exists, its files are
    just older, so the dry run returns "no tests collected" — which 4d teaches the agent to expect.
    It comes back green at plan and fails at build. One live item spent a whole revise cycle on it.
    The plan skill states the rule ("never `cd`, never an absolute path"); nothing enforced it."""
    import re
    from pathlib import Path as _P
    from ....core import artifacts as _arts
    plan_path = _P(item_dir) / "artifacts" / _arts.artifact_file("plan")
    if not plan_path.is_file():
        return ""
    try:
        checks = _arts.parse_vet_plan(plan_path.read_text(encoding="utf-8")).get("checks", [])
    except (OSError, ValueError):
        return ""
    def norm(s) -> str:
        return str(s).replace("\\", "/").rstrip("/")

    # Both forms. `resolve()` gives a drive and backslashes on Windows, so a plan written with
    # POSIX paths could never prefix-match it.
    roots = {norm(repo_dir)}
    try:
        roots.add(norm(_P(repo_dir).resolve()))
    except (OSError, ValueError):
        pass
    bad: list[str] = []
    for c in checks:
        for line in str(c.get("run") or "").splitlines():
            if re.search(r"(^|[;&|]\s*)cd\s", line):
                bad.append(f"{c.get('id') or '?'}: `cd` — {line.strip()[:70]}")
                continue
            for tok in re.findall(_ABS_TOKEN, line):
                if tok.startswith(_SYSTEM_ROOTS) or any(norm(tok).startswith(r) for r in roots):
                    continue
                bad.append(f"{c.get('id') or '?'}: absolute path — {tok[:60]}")
                break
    if not bad:
        return ""
    return ("\n\nLEAVES THIS ITEM'S WORKTREE — " + "; ".join(bad[:4])
            + ". Those run in the primary checkout, which sits on the anchor branch WITHOUT this "
              "item's commits, so they grade code the item never wrote. They pass here and fail at "
              "build. Every path is relative to the repo root.")


def _check_plan_commands(*, store, context_id, dev_root=None, repo_dir=None, bound_item_id=None, **_):
    async def check_plan_commands(args: dict) -> dict:
        """Smoke-test the `run:` blocks in this item's plan, and say what nothing proves.

        Records nothing."""
        item_id = _s(args, "item_id")
        if (msg := _bound_err(item_id, bound_item_id)):
            return _err(msg)
        d = _item_dir(dev_root, item_id)
        if d is None:
            return _err(f"No work-item {item_id!r} here.")
        if repo_dir is None:
            return _err("no repo to run in from this session.")
        kind = None
        try:
            from ....core.dev_knowledge import parse_md
            meta, _b = parse_md((d / "item.md").read_text(encoding="utf-8"))
            kind = meta.get("kind")
        except (OSError, ValueError):
            pass
        coverage = _stray_run_blocks(d, repo_dir) + _plan_coverage(d, kind)
        from ....daemon.services import checks as _checks
        try:
            rows = await asyncio.to_thread(_checks.dry_run, d, repo_dir)
        except Exception as e:                       # a dry run must never take the turn down
            return _err(f"dry run could not complete: {e}")
        if not rows:
            return _ok("nothing to dry-run — no check in this plan carries a `run:` block "
                       "(or this host has no sandbox)." + coverage)
        return _ok("\n".join(f"{r['check']}: {r['result']}" for r in rows)
                   + "\n\nA failing assertion is EXPECTED — the work is not built yet. What you "
                     "are looking for is a command that could not run at all (usage error, import "
                     "error, wrong path): that one will never come back green, whatever build does."
                   + coverage)
    return check_plan_commands


class RecordValidationArgs(TypedDict, total=False):
    item_id: Required[Annotated[str, "the work-item id"]]
    command: Required[Annotated[str, (((((("the command you ran, verbatim and re-runnable from the "
                                           "worktree root. The vet pass re-executes this exact "
                                           "string, so a paraphrase cannot be checked"))))))]]
    result: Required[Annotated[str, "the machine result: exit code, counts, output tail"]]
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
    result: Required[Annotated[str, "the machine result: exit code, counts, output tail"]]
    passed: Required[Annotated[bool, "did the check pass (false when deferred)"]]
    note: Annotated[str, "for a failure: expected vs actual in one line"]
    deferred: Annotated[bool, (((((("true for a check the build deferred to the owner, pending "
                                    "authorization at review. It records an intentional skip rather "
                                    "than a failure"))))))]
    met: Annotated[list[str], (((((("rubric checks only: the plan's criteria this build meets, "
                                    "verbatim. Every criterion must appear in `met` or `missed`"))))))]
    missed: Annotated[list[str], (((((("rubric checks only: the criteria it does not meet. Any "
                                       "missed criterion means `passed` is false; a rubric is a "
                                       "bar, not a score"))))))]


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
    check: Required[Annotated[str, "the failing check's plan id, verbatim"]]
    where: Required[Annotated[str, (((((("the narrowest located source: a file and line, the "
                                         "failing frame, the request that errored. Not 'in the "
                                         "parser' when you know the line"))))))]]
    why: Required[Annotated[str, (((((("the mechanism as far as the evidence supports it: what "
                                       "actually happens, not what should have"))))))]]
    unknown: Annotated[str, (((((("what you could not determine, if anything. An honest gap tells "
                                  "the next build cycle where you did not look"))))))]


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
    probed: Required[Annotated[list[str], (((((("what you examined or tried through this lens, one "
                                                "probe per entry: an input, a path, a command you "
                                                "ran, each with its outcome"))))))]]
    findings: Annotated[list[LensFindingArg],
                        (((((("what the lens found. Empty is expected and correct when there is "
                              "nothing; never manufacture one"))))))]


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
    general: Required[Annotated[str, (((((("the property of this repo the check defends, said "
                                           "without mentioning this item. If you cannot say it that "
                                           "way, it is not an entry"))))))]]


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
    item_id: Annotated[str, ((((("the work-item id. Include it at close to also get this item's "
                                 "nominations, rendered as ready-to-write entries")))))]


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
        # The LIBRARY needs no plan. Only this addendum does, and an item without one has no check
        # to nominate — a research item never writes a plan at all, and close mounts this tool. It
        # used to read the file unguarded and hand back a raw Errno 2.
        plan_path = (d / "artifacts" / _arts.artifact_file("plan")) if d is not None else None
        if d is not None and plan_path.is_file() and not _bound_err(item_id, bound_item_id):
            noms = _arts.nominations(d)
            checks = {c["id"]: c for c in _arts.parse_vet_plan(
                plan_path.read_text(encoding="utf-8")).get("checks", [])}
            blocks = [_vl.render_entry(checks[cid]) for cid in noms if cid in checks]
            if blocks:
                out.append("\n## nominated by this item — write each as an `append` op on "
                           f"doc `{_vl.LIBRARY_DOC}`, section `Available`")
                out.extend(f"\n```\n{b.rstrip()}\n```" for b in blocks)
        return _ok("\n".join(out))
    return read_verification_library
