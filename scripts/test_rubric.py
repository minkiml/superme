"""A check may carry a rubric (verification-model design §2, stage 4).

The defect this closes: a check had exactly one bar — a binary `expect` line. Plenty of real
checks want an exit code AND a judgment about what was printed, and squeezing that into one
sentence produced the unfalsifiable `expect` the soft flags were invented to catch. A rubric holds
several criteria, each judged and RECORDED separately, so a failure names which one missed rather
than reporting "2 of 3".

Two rules are enforced rather than asked for, because a partial record and a partial pass look
identical from outside: every planned criterion must be accounted for, and any missed criterion
fails the check. A rubric is the bar, not a score.

The design's other rule — no quotas ("find at least two unhandled inputs" manufactures findings) —
cannot be mechanically checked, so it lives in the plan skill and this suite pins that it is said.

Self-cleaning: temp item folders. No daemon, no spine, no network.

Run: PYTHONPATH=. python -m scripts.test_rubric
"""

import tempfile
from pathlib import Path

from superme_agent.core import artifacts as _arts
from superme_agent.daemon.services import checks as _checks

PASS = 0
ROOT = Path(__file__).resolve().parents[1]

PLAN = """# Plan — probe

## Verification plan
depth: checks
reason: probing the rubric
env: none

### err-msg
- proves: a bad date tells you which flag was wrong and what a good one looks like
- traces: user story u-1
- covers: t1
- mode: inspection
- scenario: read the error the CLI prints for a bad date
- rubric:
  - the message names the offending flag, not just "invalid input"
  - it shows the accepted format, and this one wraps
    onto a second line
  - the command exits non-zero
- expect: the message is a single line

### suite
- proves: nothing that already worked stopped working
- traces: PRD deliverable d-1
- covers: t2
- mode: command
- scenario: run the suite
- run: exit 0
- expect: every test passes and the command exits zero, with no skips
"""


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def src(rel: str) -> str:
    return (ROOT / rel).read_text()


def _item(plan: str = PLAN) -> Path:
    item = Path(tempfile.mkdtemp(prefix="rub-")) / "item"
    (item / "artifacts").mkdir(parents=True)
    (item / "artifacts" / _arts.artifact_file("plan")).write_text(plan)
    _arts.scaffold_cycle(item)
    return item


def _rubric(item: Path) -> list[str]:
    plan = (item / "artifacts" / _arts.artifact_file("plan")).read_text()
    return next(c["rubric"] for c in _arts.parse_vet_plan(plan)["checks"] if c["id"] == "err-msg")


# ── the contract ────────────────────────────────────────────────────────────────────────────────

def _lenses(d) -> None:
    """The three standing lenses, owed on every cycle before the report will write
    (verification-model §3). Not what this suite is testing — just the bar it now has to clear."""
    for ln in _arts.STANDING_LENSES:
        _arts.record_lens(d, lens=ln, probed="read the diff through this lens")

def test_the_plan_can_carry_criteria():
    vp = _arts.parse_vet_plan(PLAN)
    by_id = {c["id"]: c for c in vp["checks"]}
    r = by_id["err-msg"]["rubric"]
    ok("the criteria parse as a list, one per bullet", len(r) == 3)
    ok("…in order", r[0].startswith("the message names"))
    ok("a criterion wrapped over two lines folds into one",
       r[1] == "it shows the accepted format, and this one wraps onto a second line")
    ok("a check without a rubric has an empty one", by_id["suite"]["rubric"] == [])
    ok("expect and rubric coexist — an exit code AND a judgment",
       bool(by_id["err-msg"]["expect"]) and bool(r))
    ok("a rubric plan passes the gate", _arts.vet_plan_hard_issues(vp) == [])


def test_a_check_needs_a_bar_that_can_fail():
    """`expect` was required; now `expect` OR a rubric is. Neither is a check that cannot go red."""
    def issues(fields: str) -> list[str]:
        return _arts.vet_plan_hard_issues(_arts.parse_vet_plan(
            "## Verification plan\ndepth: checks\nreason: r\nenv: none\n\n"
            "### c1\n- proves: the product does the thing the brief asked for\n- traces: t\n- covers: t1\n- mode: inspection\n- scenario: s\n" + fields))

    ok("rubric alone is enough", issues("- rubric:\n  - it reads as a sentence\n") == [])
    ok("expect alone is still enough", issues("- expect: exits zero and prints nothing\n") == [])
    bare = issues("")
    ok("neither is refused", any("has no way to fail" in i for i in bare))
    ok("…and the refusal names both shapes",
       any("`expect`" in i and "rubric" in i for i in bare))


# ── recording ───────────────────────────────────────────────────────────────────────────────────

def test_every_criterion_must_be_accounted_for():
    item = _item()
    r = _rubric(item)
    try:
        _arts.record_verification(item, None, check="err-msg", how="read it", result="…",
                                  passed=False, met=r[:1])
        ok("a partial record is refused", False)
    except ValueError as e:
        ok("a partial record is refused", "accounted for 1" in str(e))
        ok("…because a skipped criterion reads as a judged one", "nobody knows the answer" in str(e))

    e = _arts.record_verification(item, None, check="err-msg", how="read it", result="see criteria",
                                  passed=False, met=r[:2], missed=r[2:])
    crit = _arts.verdict_rows(item)[0]["criteria"]
    ok("each criterion is recorded on its own", len(crit) == 3)
    ok("…with its own verdict", [c["met"] for c in crit] == [True, True, False])
    ok("…and its own text, so the row names WHICH one missed",
       crit[2]["text"] == "the command exits non-zero")


def test_a_rubric_is_the_bar_not_a_score():
    item = _item()
    r = _rubric(item)
    try:
        _arts.record_verification(item, None, check="err-msg", how="read it", result="…",
                                  passed=True, met=r[:2], missed=r[2:])
        ok("a missed criterion cannot pass", False)
    except ValueError as e:
        ok("a missed criterion cannot pass", "cannot pass" in str(e))
        ok("…and says why in one line", "not a score" in str(e))
    _arts.record_verification(item, None, check="err-msg", how="read it", result="all met",
                              passed=True, met=r)
    ok("all met passes", _arts.verdict_rows(item)[0]["passed"] is True)


def test_criteria_on_a_check_with_no_rubric_are_refused():
    item = _item()
    try:
        _arts.record_verification(item, None, check="suite", how="ran", result="exit 0",
                                  passed=True, met=["something nobody planned"])
        ok("criteria against a rubric-less check are refused", False)
    except ValueError as e:
        ok("criteria against a rubric-less check are refused", "declares no rubric" in str(e))


def test_the_kernel_leaves_judged_checks_alone():
    """A rubric check's verdict contains a judgment, and a machine entry is final — recording the
    exit code would lock out the criteria half."""
    item = _item(PLAN.replace("### err-msg\n", "### err-msg\n- run: exit 0\n"))
    ok("a rubric check is never kernel-run even with a run block",
       [c["id"] for c in _checks.runnable_checks(item)] == ["suite"])


# ── the surfaces ────────────────────────────────────────────────────────────────────────────────

def test_the_reader_sees_which_criterion_missed():
    item = _item()
    r = _rubric(item)
    _arts.record_verification(item, None, check="err-msg", how="read it", result="see criteria",
                              passed=False, met=r[:2], missed=r[2:])
    _arts.record_diagnosis(item, check="err-msg", where="cli.py:31",
                           why="the error path returns before the exit code is set")
    _arts.record_verification(item, None, check="suite", how="ran", result="exit 0", passed=True)
    _lenses(item)
    text = Path(_arts.write_vet_user_report(item, None)["path"]).read_text()
    # The score left the vet report with the rest of the per-check table; it rides the Proof row,
    # criterion by criterion, where the Task tab shows it. What the REPORT owes is that the rubric
    # check came back red at all — machine-authored, so vet cannot write around it.
    ok("a missed rubric reaches the owner's report as a failure",
       "## What didn't hold" in text and "cli.py:31" in text)
    ok("…and the criteria themselves stay judged one by one on the record",
       [c["met"] for c in next(v for r_ in _arts.proof_rows(item)
                               for v in r_["verified"] if v["check"] == "err-msg")["criteria"]]
       == [True, True, False])

    rows = _arts.proof_rows(item)
    v = next(v for r_ in rows for v in r_["verified"] if v["check"] == "err-msg")
    ok("the Proof row carries the judged criteria", len(v["criteria"]) == 3)
    planned = next(v for r_ in rows for v in r_["verified"] if v["check"] == "suite")
    ok("…and a check with no rubric carries none", planned["criteria"] == [])
    # Found while writing this suite: a `covers:` naming a task the plan never declared routed the
    # row to a bucket nothing read, so the check vanished from Proof entirely.
    ok("a check covering an undeclared task is shown item-wide, never dropped",
       any(v["check"] == "err-msg" for r_ in rows if r_["task"] == "" for v in r_["verified"]))


def test_the_plan_gate_shows_the_rubric_before_anything_runs():
    item = _item()
    v = next(v for r in _arts.proof_rows(item) for v in r["verified"] if v["check"] == "err-msg")
    ok("the criteria are readable at the plan gate", len(v["rubric"]) == 3)
    ok("…while the row has not run", v["ran"] is False and v["criteria"] == [])


def test_wiring():
    ok("the surface can render both the plan's criteria and the judgment",
       "rubric: list[str]" in src("superme_agent/daemon/schemas/dev/gates.py")
       and "criteria: list[Criterion]" in src("superme_agent/daemon/schemas/dev/gates.py")
       and "v.rubric.map" in src("web/frontend/src/features/dev/WorkItemModal.tsx"))
    tools = src("superme_agent/harness/tools/dev_tools.py")
    ok("vet can pass its per-criterion judgment", '"met"' in tools and '"missed"' in tools)
    plan_skill = src("superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md")
    ok("the plan skill forbids quotas — they manufacture findings",
       "No quotas" in plan_skill and "manufactures findings" in plan_skill)
    ok("…and requires a criterion that can come back missed", "come back missed" in plan_skill)
    vet_skill = src("superme_agent/harness/plugins/superme-dev/skills/vet/SKILL.md")
    ok("the vet skill states the bar", "`proves:` is the real bar" in vet_skill)
    tmpl = src("superme_agent/harness/plugins/superme-dev/skills/plan/templates/plan-template.md")
    ok("the template offers the rubric as optional, with its bullets",
       "- rubric: <fill:optional" in tmpl and "no quota" in tmpl)


def main() -> None:
    test_the_plan_can_carry_criteria()
    test_a_check_needs_a_bar_that_can_fail()
    test_every_criterion_must_be_accounted_for()
    test_a_rubric_is_the_bar_not_a_score()
    test_criteria_on_a_check_with_no_rubric_are_refused()
    test_the_kernel_leaves_judged_checks_alone()
    test_the_reader_sees_which_criterion_missed()
    test_the_plan_gate_shows_the_rubric_before_anything_runs()
    test_wiring()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
