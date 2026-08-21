"""Build⟷vet loop stage-2 gate test — THE VET PLAN CONTRACT (build-vet-loop-design §3, §9 step 2).

Covers: the implementation-plan template carries `## Inner checks` + `## Vet plan` (and no legacy
slot); the parser round-trips the §3.2 worked example; every §3.4 HARD rule blocks and every SOFT
rule flags-without-blocking; plan self_check enforces hard rules at the gate while legacy plans
stay green read-only; inner checks parse to runnable command lines; check ids join the evidence
ledger for free; the pre-main gate brief surfaces depth/reason and the vagueness flags; `proves:`
is required and reaches the proof rows unaltered.
Self-cleaning (tempdirs). No daemon needed.

Run: PYTHONPATH=. python scripts/test_bv_s2.py
"""

import tempfile
from pathlib import Path

from superme_agent.core import artifacts as A
from superme_agent.core import gate_briefs as GB

ROOT = Path(__file__).resolve().parents[1]
PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


# The §3.2 worked example, verbatim shape.
GOOD_PLAN = """# Plan — tally

## Approach
Thin argparse over a storage module.

## Tasks
- [ ] build it

## Inner checks
- `python -m pytest tests/ -q`
- `ruff check .`

## Vet plan
depth: scenarios
reason: user-facing CLI round-trip — the deliverable is add-then-list
env: playground-cli

### ledger-add-roundtrip
- proves: an expense you add shows up in the list, exactly as you entered it
- traces: d-ledger — "As a user I want to add an expense so that it's recorded"
- mode: interaction
- scenario: run `python -m tally add 12.50 groceries --note lunch`, then `python -m tally list`
- expect: list output has exactly one row reading 12.50 / groceries / lunch

### ledger-amount-precision
- proves: money adds up to the cent, with no floating-point drift in the total
- traces: spec D-003 — amounts stored as integer cents, never float
- mode: command
- scenario: `python -m tally add 0.10 x && python -m tally add 0.20 x && python -m tally list`
- expect: total prints 0.30 exactly (not 0.30000000000000004); exit 0
"""


def plan_with(header: str, checks: str) -> str:
    return ("# Plan — t\n\n## Approach\nx\n\n## Tasks\n- [ ] t\n\n"
            "## Inner checks\n- `pytest -q`\n\n## Vet plan\n" + header + checks)


CHECK_OK = """
### one-check
- proves: the deliverable behaves the way the brief said it would
- traces: d-x — the deliverable
- mode: command
- scenario: run `pytest -k one` in the worktree
- expect: exit 0 and the summary line reads "1 passed"
"""


def test_template() -> None:
    print("template — the new sections replace the escape hatch")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        p = Path(A.scaffold(d, "plan", title="T", item_kind="implementation")["path"])
        text = p.read_text()
        ok("impl plan scaffolds Design + Verification plan",
           "## Design" in text and "## Verification plan" in text)
        ok("the 'where possible' slot is gone", "Validation criteria" not in text)
        ok("required sections include both",
           set(A.required_sections("plan", "implementation"))
           >= {"Design", "Verification plan"})
        r = Path(A.scaffold(d / "r", "plan", title="T", item_kind="research")["path"])
        ok("research plan untouched (no verification plan — O6)",
           "Verification plan" not in r.read_text() and "Done criteria" in r.read_text())


def test_parser() -> None:
    print("parser — the §3.2 example round-trips")
    vp = A.parse_vet_plan(GOOD_PLAN)
    ok("header fields parse", vp["present"] and vp["depth"] == "scenarios"
       and vp["env"] == "playground-cli" and vp["reason"].startswith("user-facing"))
    ok("both checks parse with ids", [c["id"] for c in vp["checks"]]
       == ["ledger-add-roundtrip", "ledger-amount-precision"])
    c = vp["checks"][0]
    ok("check fields land verbatim", c["mode"] == "interaction"
       and c["traces"].startswith("d-ledger") and "12.50 / groceries / lunch" in c["expect"])
    ok("clean plan has zero hard issues", A.vet_plan_hard_issues(vp) == [],
       str(A.vet_plan_hard_issues(vp)))
    ok("clean plan has zero soft flags", A.vet_plan_soft_flags(vp) == [])
    ok("no section → present False, one hard issue",
       not A.parse_vet_plan("# Plan\n\n## Approach\nx\n")["present"]
       and A.vet_plan_hard_issues(A.parse_vet_plan("## Approach\nx\n"))
       == ["missing required section '## Verification plan'"])
    ok("unfilled <fill:…> values read as absent",
       A.parse_vet_plan("## Vet plan\ndepth: <fill:none | checks>\n")["depth"] == "")


def hard(text: str) -> list[str]:
    return A.vet_plan_hard_issues(A.parse_vet_plan(text))


def test_hard_rules() -> None:
    print("§3.4 HARD — every structural rule blocks")
    ok("illegal depth", any("depth must be one of" in i for i in
       hard(plan_with("depth: full\nreason: r\nenv: none\n", CHECK_OK))))
    ok("missing reason (even for none)", any("reason is required" in i for i in
       hard(plan_with("depth: none\nenv: none\n", ""))))
    ok("depth none is first-class — reason alone suffices",
       hard(plan_with("depth: none\nreason: pure comment change, no observable surface\nenv: none\n", "")) == [])
    ok("depth none + declared checks contradict", any("depth is none but" in i for i in
       hard(plan_with("depth: none\nreason: r\nenv: none\n", CHECK_OK))))
    ok("depth scenarios with zero checks", any("requires at least one" in i for i in
       hard(plan_with("depth: scenarios\nreason: r\nenv: e\n", ""))))
    ok("missing field named per check", any("'one-check': missing `scenario`" in i for i in
       hard(plan_with("depth: checks\nreason: r\nenv: none\n",
                      "\n### one-check\n- proves: the product does the thing the brief asked for\n"
                      "- traces: t\n- mode: command\n- expect: exits zero\n"))))
    # `expect` alone stopped being the required bar (verification-model §2): a check needs `expect`,
    # a rubric, or both — what is enforced is that it can come back RED, not which shape says so.
    ok("a check with no bar at all is refused", any("has no way to fail" in i for i in
       hard(plan_with("depth: checks\nreason: r\nenv: none\n",
                      "\n### one-check\n- proves: the product does the thing the brief asked for\n"
                      "- traces: t\n- mode: command\n- scenario: s\n"))))
    ok("…and a rubric satisfies it without `expect`",
       hard(plan_with("depth: checks\nreason: r\nenv: none\n",
                      "\n### one-check\n- proves: the product does the thing the brief asked for\n"
                      "- traces: t\n- mode: inspection\n- scenario: s\n"
                      "- rubric:\n  - the message names the offending flag\n")) == [])
    ok("illegal mode", any("mode must be one of" in i for i in
       hard(plan_with("depth: checks\nreason: r\nenv: none\n",
                      CHECK_OK.replace("mode: command", "mode: vibes")))))
    ok("interaction without env blocked", any("needs an `env` recipe" in i for i in
       hard(plan_with("depth: scenarios\nreason: r\nenv: none\n",
                      CHECK_OK.replace("mode: command", "mode: interaction")))))
    ok("command without env is fine",
       hard(plan_with("depth: checks\nreason: r\nenv: none\n", CHECK_OK)) == [])
    ok("non-slug id (it keys the ledger)", any("lowercase slug" in i for i in
       hard(plan_with("depth: checks\nreason: r\nenv: none\n",
                      CHECK_OK.replace("### one-check", "### One Check!")))))
    ok("duplicate ids", any("duplicate id" in i for i in
       hard(plan_with("depth: checks\nreason: r\nenv: none\n", CHECK_OK + CHECK_OK))))


def test_soft_rules() -> None:
    print("§3.4 SOFT — vagueness flags, never blocks")
    vague = plan_with("depth: checks\nreason: r\nenv: none\n",
                      CHECK_OK.replace('exit 0 and the summary line reads "1 passed"',
                                       "the add command works correctly"))
    vp = A.parse_vet_plan(vague)
    ok("'works correctly' flagged", any("non-falsifiable" in f for f in A.vet_plan_soft_flags(vp)))
    ok("…but it does NOT block", A.vet_plan_hard_issues(vp) == [])
    short = plan_with("depth: checks\nreason: r\nenv: none\n",
                      CHECK_OK.replace('exit 0 and the summary line reads "1 passed"', "exit 0"))
    ok("too-short expect flagged", any("very short" in f for f in
       A.vet_plan_soft_flags(A.parse_vet_plan(short))))
    ok("a falsifiable expect passes clean",
       A.vet_plan_soft_flags(A.parse_vet_plan(GOOD_PLAN)) == [])
    # BV-A2 small-fix: a check that targets the RETIRED doc spec.md is flagged (can't pass the loop).
    retired = plan_with("depth: checks\nreason: r\nenv: none\n",
                        CHECK_OK.replace("run `pytest -k one` in the worktree",
                                         "grep the four anchor docs including spec.md for `report`"))
    ok("retired-doc (spec.md) reference flagged",
       any("RETIRED" in f and "spec.md" in f for f in
           A.vet_plan_soft_flags(A.parse_vet_plan(retired))))
    ok("…but the bare word 'spec' (spec D-003) does NOT flag",
       not any("RETIRED" in f for f in A.vet_plan_soft_flags(A.parse_vet_plan(GOOD_PLAN))))


def test_proves_is_the_human_field() -> None:
    """`proves:` — the one check field written for the OWNER (human-report phase, slice 1). Every
    other field serves executing or judging, so before this the reports and the vetter each had to
    infer what a green MEANT from a shell command, separately. Declared once, at plan."""
    print("proves — the check says what a pass MEANS, in the owner's terms")
    vp = A.parse_vet_plan(GOOD_PLAN)
    ok("the field parses off a check",
       vp["checks"][0]["proves"].startswith("an expense you add shows up"))
    ok("it is HARD — a check with no meaning cannot pass the gate",
       any("'one-check': missing `proves`" in i for i in
           hard(plan_with("depth: checks\nreason: r\nenv: none\n",
                          "\n### one-check\n- traces: t\n- mode: command\n- scenario: s\n"
                          "- expect: exits zero and prints nothing\n"))))
    # …unlike `covers`, which stays soft: a missing join lands the check in the item-wide row,
    # while a missing meaning leaves every downstream reader guessing.
    ok("…where `covers` is still not required",
       not any("covers" in i for i in hard(plan_with("depth: checks\nreason: r\nenv: none\n",
                                                     CHECK_OK))))
    machine = plan_with("depth: checks\nreason: r\nenv: none\n",
                        CHECK_OK.replace("the deliverable behaves the way the brief said it would",
                                         "the suite passes and exit code is 0"))
    mvp = A.parse_vet_plan(machine)
    ok("command-talk is FLAGGED", any("command's terms" in f for f in A.vet_plan_soft_flags(mvp)))
    ok("…but never blocks — phrasing is a judgment, and a human is at this gate",
       A.vet_plan_hard_issues(mvp) == [])
    ok("a one-word proves is flagged as too short to read alone",
       any("very short" in f and "proves" in f for f in A.vet_plan_soft_flags(A.parse_vet_plan(
           plan_with("depth: checks\nreason: r\nenv: none\n",
                     CHECK_OK.replace("the deliverable behaves the way the brief said it would",
                                      "it is quiet"))))))
    tmpl = (ROOT / "superme_agent/harness/plugins/superme-dev/skills/plan/templates/"
            "plan-template.md").read_text()
    ok("the template asks for it, in the owner's terms", "- proves: <fill:" in tmpl
       and "never \"exit code is 0\"" in tmpl)
    skill = (ROOT / "superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md").read_text()
    ok("the plan skill gives the cover-the-block test", "`proves:`" in skill
       and "cover the rest of the block" in skill)
    vet = (ROOT / "superme_agent/harness/plugins/superme-dev/skills/vet/SKILL.md").read_text()
    ok("the vet skill reads it before running anything",
       "`proves:` is the real bar" in vet)
    # The surface's row carries it, so the Task tab never re-derives the meaning from `run:`.
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "artifacts").mkdir()
        (d / "artifacts" / "plan.md").write_text(GOOD_PLAN)
        row = next(r for r in A.proof_rows(d) if r["task"] == "")
        ok("proof rows carry `proves` from the plan, unaltered",
           any(v["proves"].startswith("an expense you add shows up")
               for v in row["verified"]))


def test_inner_checks() -> None:
    print("inner checks — line-oriented commands")
    ok("commands parse, backticks stripped",
       A.parse_inner_checks(GOOD_PLAN) == ["python -m pytest tests/ -q", "ruff check ."])
    ok("unfilled slot lines skipped",
       A.parse_inner_checks("## Inner checks\n- `<fill:cmd>`\n- `real --check`\n")
       == ["real --check"])


def test_self_check_gate() -> None:
    print("self_check — the pre-main gate enforces the contract")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        def write_plan(text: str) -> None:
            (d / "artifacts").mkdir(exist_ok=True)
            (d / "artifacts" / "plan.md").write_text("---\nartifact: plan\n---\n" + text)

        write_plan(GOOD_PLAN)
        ok("the §3.2 example is gate-ready",
           A.self_check(d, "plan", item_kind="implementation") == [],
           str(A.self_check(d, "plan", item_kind="implementation")))
        write_plan(plan_with("depth: scenarios\nreason: r\nenv: e\n", ""))
        ok("hard issue blocks the gate", any("requires at least one" in i for i in
           A.self_check(d, "plan", item_kind="implementation")))
        vague = plan_with("depth: checks\nreason: r\nenv: none\n",
                          CHECK_OK.replace('exit 0 and the summary line reads "1 passed"',
                                           "it works correctly and handles all edge cases nicely"))
        write_plan(vague)
        ok("soft flag does NOT block the gate",
           A.self_check(d, "plan", item_kind="implementation") == [])
        # Legacy: a pre-vet-loop plan stays green read-only; a NEW plan can't dodge by dropping both.
        write_plan("# Plan — old\n\n## Approach\nx\n\n## Tasks\n- [x] t\n\n"
                   "## Validation criteria\nthe check command exits 0\n")
        ok("legacy plan (Validation criteria) accepted read-only",
           A.self_check(d, "plan", item_kind="implementation") == [])
        write_plan("# Plan — new\n\n## Approach\nx\n\n## Tasks\n- [x] t\n")
        issues = A.self_check(d, "plan", item_kind="implementation")
        ok("dropping BOTH shapes is caught",
           any("Inner checks" in i for i in issues) and any("Vet plan" in i for i in issues))


def test_ledger_join() -> None:
    print("the join — plan check ids ARE evidence-ledger keys (no new store)")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        vp = A.parse_vet_plan(GOOD_PLAN)
        for c in vp["checks"]:
            A.record_verification(d, None, check=c["id"], how=c["scenario"],
                              result="observed as expected", passed=True)
        latest = {e["check"] for e in A.evidence_entries(d)}
        ok("every plan check id resolves in the ledger",
           latest == {c["id"] for c in vp["checks"]})
        st = A.evidence_status(d, None)
        ok("evidence_status is the loop condition, already written", st["status"] == "passed")
        A.record_verification(d, None, check="ledger-add-roundtrip", how="re-ran the round-trip",
                          result="two rows; the second is a duplicate", passed=False)
        st = A.evidence_status(d, None)
        ok("a failing check surfaces BY PLAN ID", st["status"] == "failed"
           and st["failed_checks"] == ["ledger-add-roundtrip"])


def test_gate_state_surface() -> None:
    """The pre-main gate's vet-plan judgment, as the owner now reads it. The prose brief that used to
    carry these warnings is gone (slice 6) — a soft flag is a NAMED, non-blocking check row, which is
    what makes the difference between fatal and advisory visible instead of tone-of-voice."""
    print("pre-main gate state — the depth/reason judgment surfaces as a non-blocking check row")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        item_dir = d / "item"
        (item_dir / "artifacts").mkdir(parents=True)
        vague = GOOD_PLAN.replace("total prints 0.30 exactly (not 0.30000000000000004); exit 0",
                                  "the totals work correctly across all the usual cases")
        (item_dir / "artifacts" / "plan.md").write_text("---\nartifact: plan\n---\n" + vague)
        item = {"id": "abc123def456", "title": "t", "kind": "implementation", "phase": "plan",
                "status": "awaiting_human"}
        s = GB.gate_state(item, item_dir, d / "root", None, all_items=[item], events=[])
        # The gate carries CHECK ROWS and nothing else — the `facts` chip list is gone (owner,
        # 2026-08-02: every chip restated something already on the drilldown). The depth judgment
        # still reaches the owner, in the row that can act on it.
        ok("a gate publishes no separate facts list", "facts" not in s)
        sharp = next(c for c in s["checks"] if c["criterion"] == "vet_plan_sharp")
        ok("a vague expect fails vet_plan_sharp, with the reason inline",
           not sharp["ok"] and "non-falsifiable" in sharp["detail"])
        ok("...and it does NOT block: a human is present, the one fail-open that's safe",
           not sharp["blocking"] and s["blocked_by"] == [])
        none_plan = ("# Plan — t\n\n## Approach\nx\n\n## Tasks\n- [ ] t\n\n"
                     "## Inner checks\n- `pytest -q`\n\n## Vet plan\n"
                     "depth: none\nreason: comment-only change, nothing observable\nenv: none\n")
        (item_dir / "artifacts" / "plan.md").write_text("---\nartifact: plan\n---\n" + none_plan)
        s = GB.gate_state(item, item_dir, d / "root", None, all_items=[item], events=[])
        sharp = next(c for c in s["checks"] if c["criterion"] == "vet_plan_sharp")
        # The warning says what actually happens (slice 5b): the vet pass RUNS and confirms there is
        # nothing to check. It used to promise "NO vet pass will run", which nothing implemented —
        # the run fired, recorded nothing, and the driver halted the item as unverified.
        ok("depth none warns that no CHECK runs — not that no vet pass runs",
           "NO check will run" in sharp["detail"] and "NO vet pass will run" not in sharp["detail"])
        ok("depth none is stated, not a refusal — the row names the depth and lets Approve stand",
           "`none`" in sharp["detail"] and not sharp["blocking"] and s["blocked_by"] == [])


if __name__ == "__main__":
    test_template()
    test_parser()
    test_hard_rules()
    test_soft_rules()
    test_proves_is_the_human_field()
    test_inner_checks()
    test_self_check_gate()
    test_ledger_join()
    test_gate_state_surface()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")
