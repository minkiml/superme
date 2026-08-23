"""The seam between what a skill INSTRUCTS and what the run can do.

A tool a skill names must be registered, in that phase's scope, and policy-allowed. Each file is
correct alone, so only the join can be wrong.

Run: PYTHONPATH=. python -m scripts.test_phase_contract
"""

import re
from pathlib import Path

from superme_agent.harness.policy import is_safe
from superme_agent.harness.tools.dev_tools import DEV_TOOLS, TOOL_SCOPES
from scripts.sources import src

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "superme_agent/harness/plugins/superme-dev/skills"
PASS = 0

# Each fires as a background run under a skill of the same name, with nobody to approve.
PHASES = ("triage", "plan", "build", "vet", "review", "close", "investigate")

# Reports the drilldown reads at `<item_dir>/reports/report-<phase>.md`.
REPORTED = ("triage", "plan", "build", "vet", "review", "close", "investigate")

# Scopes mounted on a run with NOBODY TO ASK. The chat scopes are excluded: a human approves each
# call there.
BACKGROUND_SCOPES = (*PHASES, "itemize", "deputy", "handoff", "resolve")

# Phases whose report is written from a path named in prose. EMPTY, and the ratchet is the value.
NO_PEN_YET: set[str] = set()


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def skill_text(phase: str) -> str:
    p = SKILLS / phase / "SKILL.md"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def tools_named_by(phase: str) -> set[str]:
    """Registered tool names this skill mentions.

    Intersecting with the catalogue is what keeps it precise: a skill's prose is full of backticked
    words."""
    catalogue = {t.name for t in DEV_TOOLS}
    words = set(re.findall(r"`([a-z][a-z0-9_]{3,})`?\(?", skill_text(phase)))
    return words & catalogue


def test_a_named_tool_is_a_tool_the_phase_can_call():
    for phase in PHASES:
        named = tools_named_by(phase)
        if not named:
            continue
        scope = set(TOOL_SCOPES.get(phase, ()))
        unscoped = sorted(named - scope)
        refused = sorted(t for t in named & scope if not is_safe(f"mcp__dev__{t}", {}))
        ok(f"{phase}: every tool it names is mounted in its own scope — missing {unscoped}",
           not unscoped)
        ok(f"{phase}: …and allowed by the policy, so a background run can actually call it "
           f"— refused {refused}", not refused)


def test_a_background_scope_mounts_nothing_it_cannot_use():
    """The inverse check: a tool nobody's prose mentions.

    A scope with no human behind it must mount only what can proceed."""
    for scope in BACKGROUND_SCOPES:
        mounted = set(TOOL_SCOPES.get(scope, ()))
        refused = sorted(t for t in mounted if not is_safe(f"mcp__dev__{t}", {}))
        ok(f"{scope}: every tool it mounts can actually proceed unattended — refused {refused}",
           not refused)


def test_every_report_the_reader_asks_for_has_a_writer():
    """The reader looks in exactly one place. Whoever writes the report has to agree with it, and
    the only way to guarantee that is for code to build the path."""
    dev_tools_src = src("superme_agent/harness/tools/dev_tools.py")
    penned, prose = set(), set()
    for phase in REPORTED:
        if f"file_{phase}_report" in dev_tools_src:
            penned.add(phase)
        if f"reports/report-{phase}.md" in skill_text(phase):
            prose.add(phase)

    ok(f"the phases with a pen own their path in code — {sorted(penned)}",
       penned == set(REPORTED) - NO_PEN_YET)
    # A skill may name what the pen produces; what matters is that it routes the agent THROUGH the
    # pen.
    unrouted = sorted(p for p in penned if f"file_{p}_report" not in skill_text(p))
    ok(f"…and each one's skill names the pen, so the write never goes through prose — "
       f"missing {unrouted}", not unrouted)
    # The ratchet. Never assert the gap is fine; assert it is not growing.
    penless = set(REPORTED) - penned
    ok(f"the pen-less list has not grown — {sorted(penless)}", penless <= NO_PEN_YET)
    ok("…and every reported phase is accounted for, penned or listed",
       penned | NO_PEN_YET == set(REPORTED))
    ok(f"…and each pen-less phase still names its path, so the report is at least attempted — "
       f"{sorted(penless & prose)}", penless <= prose)


def main() -> None:
    print("phase contract — what a skill instructs vs what the run can do\n")
    test_a_named_tool_is_a_tool_the_phase_can_call()
    print()
    test_a_background_scope_mounts_nothing_it_cannot_use()
    print()
    test_every_report_the_reader_asks_for_has_a_writer()
    print(f"\nALL GREEN — {PASS} checks passed.")
    if NO_PEN_YET:
        print(f"\nOPEN: {len(NO_PEN_YET)} phase(s) still write their report from a path named in "
              f"prose — {', '.join(sorted(NO_PEN_YET))}. Each is one confused path resolution away "
              f"from filing where the drilldown does not look.")


if __name__ == "__main__":
    main()
