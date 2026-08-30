"""Validation is build's, and its proof is vet's.
"""

import shutil
import tempfile
from pathlib import Path

from superme_agent.core import artifacts as A
from scripts.sources import src

ROOT = Path(__file__).resolve().parents[1]
PASS = 0
_TMP: list[Path] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def item() -> Path:
    d = Path(tempfile.mkdtemp(prefix="val-")) / "item"
    (d / "artifacts").mkdir(parents=True)
    _TMP.append(d.parent)
    A.scaffold_cycle(d, title="a cycle")
    return d


def test_a_run_is_recorded_as_data() -> None:
    print("build's validation run — recorded, not narrated")
    d = item()
    e = A.record_validation(d, None, command="python3 -m unittest discover -s tests",
                            result="Ran 106 tests, OK", passed=True)
    ok("the recorder returns the entry it wrote",
       e["command"] == "python3 -m unittest discover -s tests" and e["passed"] is True)
    runs = A.validation_runs(d)
    ok("…and the reader finds exactly it", len(runs) == 1
       and runs[0]["command"] == "python3 -m unittest discover -s tests"
       and runs[0]["result"] == "Ran 106 tests, OK" and runs[0]["passed"] is True)
    ok("a run carries the tree it ran against — a claim about code that has moved is not a claim "
       "about this code", "fingerprint" in runs[0])
    A.record_validation(d, None, command="ruff check .", result="1 error", passed=False, task="t1")
    runs = A.validation_runs(d)
    ok("a FAILED run is recorded too — a red build fixed is part of the cycle",
       runs[-1]["passed"] is False and runs[-1]["task"] == "t1")
    ok("…and the ledger is append-only, in record order", len(runs) == 2)


def test_the_command_is_the_identity() -> None:
    """Vet re-executes the recorded string verbatim.

    A record with no command cannot be re-run."""
    print("the command IS the record")
    d = item()
    try:
        A.record_validation(d, None, command="   ", result="fine", passed=True)
        ok("a run with no command is refused", False)
    except ValueError as e:
        ok("a run with no command is refused", "COMMAND" in str(e))
    A.record_validation(d, None, command="pytest  -q\n  tests/", result="ok", passed=True)
    ok("…and a recorded command is normalised to one re-runnable line",
       A.validation_runs(d)[0]["command"] == "pytest -q tests/")


def test_the_machine_lane_and_the_prose_coexist() -> None:
    """Validation carries both build's narrative and the machine rows."""
    print("machine lane beside prose — each read by its own reader")
    d = item()
    A.record_validation(d, None, command="pytest -q", result="12 passed", passed=True)
    path = Path(A.cycle_reports(d)[-1]["path"])
    path.write_text(path.read_text(encoding="utf-8").replace(
        "## Validation", "## Validation\n- t1 — 12 unit tests pass\n- the suite is green", 1), encoding="utf-8")
    body = A.split_sections(path.read_text(encoding="utf-8"))["Validation"]
    by_task, loose = A._tagged_bullets(body)
    ok("the per-task bullet still reaches the Task tab", by_task == {"t1": ["12 unit tests pass"]})
    ok("…and the fence's own lines never leak into the prose",
       loose == ["the suite is green"])
    ok("…while the machine lane still reads clean", len(A.validation_runs(d)) == 1)


def test_an_entry_stands_apart_from_the_one_before_it() -> None:
    """A blank line between entries.

    Packed back to back they read as one."""
    print("the machine lanes are readable")
    d = item()
    for n in range(3):
        A.record_validation(d, None, command=f"pytest -q tests/t{n}.py", result="exit 0",
                            passed=True)
    body = A.split_sections((d / "artifacts" / A.cycle_reports(d)[-1]["path"].split("/")[-1]
                              ).read_text(encoding="utf-8"))["Validation"]
    fence = A._fenced_blocks(body, lang=A.VALIDATION_FENCE)[0].strip("\n").splitlines()
    heads = [i for i, ln in enumerate(fence) if ln.startswith("### ")]
    ok("every entry after the first opens on a blank line",
       all(fence[i - 1].strip() == "" for i in heads[1:]), str(fence))
    ok("…and none of them is preceded by two", all(fence[i - 2].strip() for i in heads[1:] if i > 1))
    ok("…while the reader still counts every one", len(A.validation_runs(d)) == 3)
    # The first entry must not be pushed off the fence's opening line — an empty block gets no
    # gap.
    ok("a fence opens straight onto its first entry", heads and heads[0] == 0)


def test_the_two_lanes_never_read_each_other() -> None:
    """Vet's checks and build's validation are separate lanes."""
    print("two machine lanes, one file, no crossing")
    d = item()
    A.record_validation(d, None, command="pytest -q", result="12 passed", passed=True)
    A.record_verification(d, None, check="c1", how="ran it", result="exit 0", passed=True)
    ok("build's runs hold only build's", [r["command"] for r in A.validation_runs(d)] == ["pytest -q"])
    ok("…and the evidence ledger holds only vet's",
       [e["check"] for e in A.evidence_entries(d)] == ["c1"])


def test_the_suite_is_refused_as_a_check() -> None:
    """The suite is build's validation.

    As a vet-plan check it runs the wrong lane."""
    print("the vet plan refuses the project's own test suite")
    for cmd in ("python3 -m unittest discover -s tests", "pytest -q", "npm test",
                "go test ./...", "cargo test", "pytest -q && echo done"):
        ok(f"`{cmd}` reads as the whole suite", A.is_whole_suite_run(cmd))
    for cmd in ("python3 -m unittest tests.test_ledger -k QuietFlagTest",
                "pytest tests/test_csv.py::test_note_roundtrip", "pytest -k csv",
                "python3 -c \"import tally; print(1)\"", "./scripts/smoke.sh"):
        ok(f"…and `{cmd}` does not", not A.is_whole_suite_run(cmd))

    def plan(run: str) -> dict:
        return A.parse_vet_plan(
            "# Plan\n\n## Verification plan\ndepth: checks\nreason: r\nenv: none\n\n"
            "### c1\n- proves: the entries come back as parseable CSV rows.\n- traces: t\n"
            f"- covers: t1\n- mode: command\n- scenario: s\n- run: {run}\n- expect: exit 0\n")
    issues = A.vet_plan_hard_issues(plan("python3 -m unittest discover -s tests"))
    ok("a suite-shaped check BLOCKS the plan gate",
       any("whole test suite" in i for i in issues))
    ok("…and the refusal says where it belongs and what to do instead",
       any("BUILD's validation" in i and "narrow the command" in i for i in issues))
    ok("a narrowed one passes untouched",
       not A.vet_plan_hard_issues(plan("pytest tests/test_csv.py::test_note_roundtrip")))
    ok("…and so does a check with no `run:` at all",
       not A.vet_plan_hard_issues(A.parse_vet_plan(
           "# Plan\n\n## Verification plan\ndepth: checks\nreason: r\nenv: none\n\n"
           "### c1\n- proves: the output reads correctly to a human.\n- traces: t\n"
           "- mode: inspection\n- scenario: s\n- expect: it matches the sample above\n")))
    # …and it can no longer enter the library either, whichever way someone tries.
    from superme_agent.core import verification_library as VL
    bad = VL.entry_issues("### full-suite-green\n- proves: nothing that worked before broke.\n"
                          "- traces: t\n- mode: command\n- scenario: run the suite\n"
                          "- run: python3 -m unittest discover -s tests\n- expect: exit 0\n")
    ok("the library refuses a suite entry", any("whole test suite" in i for i in bad))
    ok("…and says what a library entry IS instead",
       any("standing questions about the PRODUCT" in i for i in bad))


def test_an_inherited_check_is_not_the_planner_s_prose() -> None:
    """The sharpness lint asks the planner to sharpen what it wrote, not what it inherited."""
    print("the sharpness lint leaves inherited checks alone")
    body = ("# Plan\n\n## Verification plan\ndepth: checks\nreason: r\nenv: none\n\n"
            "### c1\n- proves: it works.\n- traces: t\n- mode: command\n- scenario: s\n"
            "- expect: exit 0\n{src}")
    ok("an authored check with a thin `expect` is still flagged",
       any("c1" in f for f in A.vet_plan_soft_flags(A.parse_vet_plan(body.format(src="")))))
    ok("…and the same check inherited from the library is not",
       A.vet_plan_soft_flags(A.parse_vet_plan(body.format(src="- source: library\n"))) == [])


def test_plan_can_smoke_test_its_own_commands() -> None:
    print("plan dry-runs the `run:` blocks it just wrote")
    from superme_agent.daemon.services import checks as C
    checks_src = src("superme_agent/daemon/services/checks.py")
    _dr = checks_src.split("def dry_run")[1].split("\ndef ")[0]
    ok("the dry run records NOTHING — at plan time a red is the expected answer",
       "record" not in _dr.split('"""')[2])
    ok("…and it runs only what the plan already committed to", "runnable_checks(item_dir)" in checks_src)
    tools = src("superme_agent/harness/tools/dev_tools.py")
    ok("the tool exists and is item-bound", '"check_plan_commands"' in tools and "_bound_err" in tools)
    policy = src("superme_agent/harness/policy.py")
    ok("…and never prompts a human mid-run", "mcp__dev__check_plan_commands" in policy)
    skill = src("superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md")
    ok("the plan skill tells the planner to use it", "check_plan_commands" in skill)
    ok("…and that a failing assertion is the EXPECTED answer", "EXPECTED" in skill)
    ok("…and forbids the suite as a check, with the narrowing remedy",
       "Never make the project's test suite a check" in skill and "narrow the command" in skill)


def test_the_contract_is_stated_where_build_reads_it() -> None:
    print("build is TOLD to record, and told why")
    skill = src("superme_agent/harness/plugins/superme-dev/skills/build/SKILL.md")
    ok("the build skill names the pen", "record_validation" in skill)
    ok("…and says the command must be re-runnable verbatim, because vet re-executes it",
       "verbatim and re-runnable" in skill or "verbatim" in skill and "re-execut" in skill)
    ok("…and that recording is not a gate on build's autonomy", "not a gate on you" in skill)
    tmpl = (ROOT / "superme_agent/harness/plugins/superme-dev/skills/build/templates/"
            "build-vet-template.md").read_text(encoding="utf-8")
    ok("the cycle report template marks the fence as machine-owned",
       "```runs" in tmpl and "never hand-edit" in tmpl)


def test_the_audit_compares_claim_to_machine() -> None:
    print("the audit — build's claim against the machine")
    d = item()
    A.record_validation(d, None, command="pytest -q", result="12 passed", passed=True)
    A.record_validation_audit(d, None, command="pytest -q", claimed=True, actual=True,
                              result="exit 0")
    ok("an agreeing audit is recorded and is NOT a discrepancy",
       len(A.validation_audit(d)) == 1 and A.validation_discrepancies(d) == [])
    A.record_validation(d, None, command="ruff check .", result="clean", passed=True)
    A.record_validation_audit(d, None, command="ruff check .", claimed=True, actual=False,
                              result="exit 1 · 3 errors")
    bad = A.validation_discrepancies(d)
    ok("a claim the machine cannot reproduce IS one",
       len(bad) == 1 and bad[0]["command"] == "ruff check ."
       and bad[0]["claimed"] is True and bad[0]["actual"] is False)
    # The newer answer replaces the older, or a resolved discrepancy gates the item forever.
    A.record_validation_audit(d, None, command="ruff check .", claimed=True, actual=True,
                              result="exit 0")
    ok("…and a re-audit after the fix clears it", A.validation_discrepancies(d) == [])


def test_an_audit_is_never_one_of_the_item_s_checks() -> None:
    """Unit tests are not the item's exam."""
    print("an audit is not a check")
    d = item()
    A.record_verification(d, None, check="c1", how="ran", result="exit 0", passed=True)
    A.record_validation_audit(d, None, command="pytest -q", claimed=True, actual=False,
                              result="exit 1")
    ok("the evidence ledger counts only the plan's checks",
       [e["check"] for e in A.evidence_entries(d)] == ["c1"])
    ok("…while the audit is still on the record", len(A.validation_audit(d)) == 1)


def test_a_broken_claim_routes_back_to_build() -> None:
    """Build and vet are autonomous, so a false green is handled without the owner."""
    print("routing — a false green is the loop's business, not the owner's")
    from superme_agent.daemon.services.loop import decide_after_vet
    live = {"id": "i1", "status": "active", "phase": "vet"}
    green = {"status": "passed"}
    clean = decide_after_vet(live, evidence=green, fingerprint="", attempts=[], spent=0,
                             budget=100, turn_error=False)
    ok("green checks with nothing else wrong still advance to review",
       clean["action"] == "review" and clean["exit"] == "converged")
    gap = [{"command": "pytest -q", "claimed": True, "actual": False, "agrees": False}]
    d = decide_after_vet(live, evidence=green, fingerprint="", attempts=[], spent=0, budget=100,
                         turn_error=False, audit_gaps=gap)
    ok("…but green checks over a claim that did not reproduce go BACK TO BUILD",
       d["action"] == "build" and d["status"] == "active")
    ok("…named as what it is, in the build's own terms",
       "`pytest -q` claim did not reproduce" in d["reason"])
    ok("…and never as a human decision", d["status"] != "awaiting_human")
    ok("…and it is listed among what failed", "validation:pytest -q" in d["failed"])


def test_the_report_carries_it_whatever_vet_writes() -> None:
    """The failures section is machine-authored off the record."""
    print("the vet report cannot write around it")
    d = item()
    plan = d / "artifacts" / "plan.md"
    plan.write_text("# Plan\n\n## Tasks\n- [ ] t1 — do it\n\n## Verification plan\n"
                    "depth: checks\nreason: r\nenv: none\n\n### c1\n- proves: the thing is true.\n"
                    "- traces: t\n- covers: t1\n- mode: command\n- scenario: s\n- expect: e\n", encoding="utf-8")
    A.record_verification(d, None, check="c1", how="ran", result="exit 0", passed=True)
    for ln in A.STANDING_LENSES:
        A.record_lens(d, lens=ln, probed="read the diff")
    A.record_validation(d, None, command="pytest -q", result="12 passed", passed=True)
    A.record_validation_audit(d, None, command="pytest -q", claimed=True, actual=False,
                              result="exit 1 · 2 failed")
    text = Path(A.write_vet_user_report(
        d, None, summary="Everything holds.", confirms="- it all works",
        looked_at="- Intent: read the diff against the brief.")["path"]).read_text(encoding="utf-8")
    ok("vet's all-clear does not suppress it", "## What didn't hold" in text)
    # The invariant, not the sentence. The wording moved and this went red on a copy edit.
    ok("…and it says a build claim did not reproduce, and how many",
       "did not hold up when re-run" in text and "1 of its checks" in text)
    ok("…without pasting the command at the owner", "pytest -q" not in text)


def main() -> None:
    try:
        test_a_run_is_recorded_as_data()
        test_the_command_is_the_identity()
        test_the_machine_lane_and_the_prose_coexist()
        test_an_entry_stands_apart_from_the_one_before_it()
        test_the_two_lanes_never_read_each_other()
        test_the_audit_compares_claim_to_machine()
        test_an_audit_is_never_one_of_the_item_s_checks()
        test_a_broken_claim_routes_back_to_build()
        test_the_report_carries_it_whatever_vet_writes()
        test_the_suite_is_refused_as_a_check()
        test_an_inherited_check_is_not_the_planner_s_prose()
        test_plan_can_smoke_test_its_own_commands()
        test_the_contract_is_stated_where_build_reads_it()
        print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")
    finally:
        for t in _TMP:
            shutil.rmtree(t, ignore_errors=True)


if __name__ == "__main__":
    main()
