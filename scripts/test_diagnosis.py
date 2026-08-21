"""Vet diagnoses; build remedies.

A failure carries where it broke and what could not be determined, so the next cycle does not
re-derive it. Vet must never name the FIX — it would then grade its own design.

Run: PYTHONPATH=. python -m scripts.test_diagnosis
"""

import tempfile
from pathlib import Path

from superme_agent.core import artifacts as _arts
from superme_agent.core.kernel_speech import build_loop_trigger

PASS = 0
ROOT = Path(__file__).resolve().parents[1]

PLAN = """# Plan — probe

## Verification plan
depth: checks
reason: probing the diagnosis duty
env: none

### date-flag
- proves: an expense recorded with an explicit date keeps that date
- traces: PRD deliverable d-1
- covers: t1
- mode: command
- scenario: add an expense with an explicit date
- expect: the row lands with the date given, not today

### suite
- proves: nothing that already worked stopped working
- traces: PRD deliverable d-1
- covers: t2
- mode: command
- scenario: run the suite
- expect: every test passes, exit status 0
"""


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def src(rel: str) -> str:
    return (ROOT / rel).read_text()


def _item() -> Path:
    item = Path(tempfile.mkdtemp(prefix="diag-")) / "item"
    (item / "artifacts").mkdir(parents=True)
    (item / "artifacts" / _arts.artifact_file("plan")).write_text(PLAN)
    _arts.scaffold_cycle(item)
    return item


def _fail(item: Path, check: str, **kw) -> None:
    _arts.record_verification(item, None, check=check, how="ran it", result="exit 1",
                              passed=False, **kw)


# ── the act ─────────────────────────────────────────────────────────────────────────────────────

def _lenses(d) -> None:
    """The three standing lenses, owed on every cycle before the report will write
    Not what this suite is testing — just the bar it now has to clear."""
    for ln in _arts.STANDING_LENSES:
        _arts.record_lens(d, lens=ln, probed="read the diff through this lens")

def test_a_diagnosis_explains_a_recorded_failure():
    item = _item()
    try:
        _arts.record_diagnosis(item, check="date-flag", where="cli.py:42", why="never passed on")
        ok("a diagnosis needs a verdict to explain", False)
    except ValueError as e:
        ok("a diagnosis needs a verdict to explain", "no recorded verdict" in str(e))

    _arts.record_verification(item, None, check="date-flag", how="ran it", result="exit 0",
                              passed=True)
    try:
        _arts.record_diagnosis(item, check="date-flag", where="cli.py:42", why="never passed on")
        ok("diagnosing a PASSING check is refused", False)
    except ValueError as e:
        ok("diagnosing a PASSING check is refused", "not failing" in str(e))
        ok("…and the refusal names where that concern does belong", "observations" in str(e))

    _fail(item, "suite")
    try:
        _arts.record_diagnosis(item, check="suite", where="", why="something")
        ok("where and why are both required", False)
    except ValueError:
        ok("where and why are both required")

    d = _arts.record_diagnosis(item, check="suite", where="tally/dates.py:12",
                               why="the parser returns today when the flag is absent",
                               unknown="whether the writer would accept an explicit date")
    ok("a diagnosis records against its check", d["check"] == "suite")
    got = _arts.diagnoses(item)["suite"]
    ok("…carrying where", got["where"] == "tally/dates.py:12")
    ok("…and why", got["why"].startswith("the parser returns today"))
    ok("…and what could not be determined", "writer would accept" in got["unknown"])


def test_a_diagnosis_is_not_a_verdict():
    """The two share a fence; nothing that counts verdicts may count a diagnosis."""
    item = _item()
    _fail(item, "suite")
    _arts.record_diagnosis(item, check="suite", where="a.py:1", why="b")
    ok("the verdict ledger holds one entry, not two", len(_arts.evidence_entries(item)) == 1)
    ok("…and it is the verdict", _arts.evidence_entries(item)[0].get("kind") is None)
    rows = _arts.verdict_rows(item)
    ok("the check has exactly one row", len(rows) == 1)
    ok("…with the cause joined onto it", rows[0]["where"] == "a.py:1")


def test_a_stale_cause_never_leads():
    """A cause from an earlier cycle is dropped: the code moved, and last cycle's reading may
    describe a bug that is already fixed and replaced by another."""
    item = _item()
    _fail(item, "suite")
    _arts.record_diagnosis(item, check="suite", where="old.py:1", why="the old cause")
    # A cycle is closed by the driver's outcome entry — scaffolding alone reopens the same file.
    _arts.append_cycle_outcome(item, evidence="failed", decision="build", reason="retry")
    _arts.scaffold_cycle(item)          # cycle 2
    _fail(item, "suite")
    ok("the new cycle's failure counts as undiagnosed",
       _arts.undiagnosed_failures(item) == ["suite"])
    ok("…and the old cause is not shown against it", _arts.verdict_rows(item)[0]["where"] == "")


# ── the teeth ───────────────────────────────────────────────────────────────────────────────────

def test_the_report_refuses_an_undiagnosed_failure():
    item = _item()
    _fail(item, "date-flag")
    _arts.record_verification(item, None, check="suite", how="ran it", result="exit 0", passed=True)
    _lenses(item)
    try:
        _arts.write_vet_user_report(item, None)
        ok("the vet report is refused while a failure has no cause", False)
    except ValueError as e:
        ok("the vet report is refused while a failure has no cause", "no diagnosis" in str(e))
        ok("…naming the check", "date-flag" in str(e))
        ok("…and saying it is not vet's job to name the fix", "that is build's" in str(e))

    _arts.record_diagnosis(item, check="date-flag", where="tally/cli.py:42",
                           why="the flag is parsed but never passed to the writer")
    r = _arts.write_vet_user_report(item, None)
    text = Path(r["path"]).read_text()
    ok("…and once diagnosed it writes", "## What didn't hold" in text)
    # MACHINE-authored off the ledger: a red result arrives regardless of what vet chose to say.
    ok("the failure leads with what STOPPED being true, not with a check id",
       "an expense recorded with an explicit date keeps that date** — did not hold" in text)
    ok("…and carries the located source the reader will actually open",
       "broke in tally/cli.py:42" in text)

    # A green cycle has nothing to explain, and an empty block would be a heading over nothing.
    item2 = _item()
    for c in ("date-flag", "suite"):
        _arts.record_verification(item2, None, check=c, how="ran it", result="exit 0", passed=True)
    _lenses(item2)
    ok("a passing cycle prints no didn't-hold block at all",
       "What didn't hold" not in Path(_arts.write_vet_user_report(item2, None)["path"]).read_text())


def test_the_cause_leads_the_next_work_order():
    t = build_loop_trigger("i1", "T", 2, "…report…", diagnoses={
        "suite": {"where": "tally/dates.py:12", "why": "the parser ignores the flag",
                  "unknown": "whether the writer accepts it"}})
    ok("the work order names where it broke", "tally/dates.py:12" in t)
    ok("…and why", "the parser ignores the flag" in t)
    ok("…and what vet could not tell", "could not determine" in t)
    ok("…and says the change is build's to reason out", "yours to reason out" in t)
    ok("…above the report, not buried in it",
       t.index("tally/dates.py:12") < t.index("--- build-vet-2.md ---"))
    ok("a cycle with no diagnosis says nothing about it",
       "What vet found" not in build_loop_trigger("i1", "T", 2, "…report…"))


# ── wiring ──────────────────────────────────────────────────────────────────────────────────────

def test_wiring():
    loop = src("superme_agent/daemon/services/loop.py")
    build = loop.split("async def _run_background_build")[1].split("\nasync def ")[0]
    ok("the build runner passes the causes into the trigger", "diagnoses=found" in build)
    ok("…for failing checks only", 'not r["passed"]' in build and 'r.get("why")' in build)

    tools = src("superme_agent/harness/tools/dev_tools.py")
    ok("vet has a pen for it", '"record_diagnosis"' in tools)
    ok("…whose description forbids the fix", "Never the fix" in tools)
    ok("…and it never prompts a human mid-loop",
       "mcp__dev__record_diagnosis" in src("superme_agent/harness/policy.py"))

    skill = src("superme_agent/harness/plugins/superme-dev/skills/vet/SKILL.md")
    ok("the vet skill carries the duty", "Diagnose every failure" in skill)
    ok("…covering the kernel's failures too", "including the checks the kernel ran" in skill)
    ok("…and forbids prescribing the change", "Never the fix" in skill)

    ok("the surface can show the cause on a failing row",
       "where: str" in src("superme_agent/daemon/schemas/dev/gates.py")
       and "v.why" in src("web/frontend/src/features/dev/WorkItemModal.tsx"))


def main() -> None:
    test_a_diagnosis_explains_a_recorded_failure()
    test_a_diagnosis_is_not_a_verdict()
    test_a_stale_cause_never_leads()
    test_the_report_refuses_an_undiagnosed_failure()
    test_the_cause_leads_the_next_work_order()
    test_wiring()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
