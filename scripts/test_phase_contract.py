"""The seam between what a skill INSTRUCTS and what the run can actually do.

Two live defects motivated this, and neither was visible to any existing suite. A report was filed
to `artifacts/reports/` because the skill named a bare relative path and the agent resolved it
against the directory it had been writing in. Then a report was not filed at all, because the tool
that files it was registered and scoped but missing from the safe-tool policy, so a background run
reached its last step and was refused with nobody to ask.

Both are the same shape: a skill tells an agent to do something the wiring cannot deliver. Suites
that read one file at a time cannot see it, because each file is individually correct — the defect
lives in the disagreement between two of them.

So this pins the joins:

  1. A tool a skill NAMES must be registered, in that phase's scope, and allowed by the policy.
     Any one of the three missing makes the instruction unfollowable.
  2. A phase whose report the drilldown reads must have a PEN that writes it. A skill that names
     the path instead is one confused resolution away from filing where nothing reads. This one is
     a RATCHET: the pen-less list may shrink, never grow.

Self-cleaning: source reads + registry imports. No daemon, no spine, no network.

Run: PYTHONPATH=. python -m scripts.test_phase_contract
"""

import re
from pathlib import Path

from superme_agent.harness.policy import is_safe
from superme_agent.harness.tools.dev_tools import DEV_TOOLS, TOOL_SCOPES

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "superme_agent/harness/plugins/superme-dev/skills"
PASS = 0

# The phases that fire as a background run with a skill of the same name. `build` and `vet` run
# from the loop, the rest from the intake runner; all of them have nobody to approve anything.
PHASES = ("triage", "plan", "build", "vet", "review", "close", "investigate")

# Reports the drilldown reads at `<item_dir>/reports/report-<phase>.md`.
REPORTED = ("triage", "plan", "build", "vet", "review", "close", "investigate")

# Scopes mounted on a run that has NOBODY TO ASK. The seven phases plus the kernel-fired turns that
# are not a phase — those were unchecked, and that is how `itemize` came to mount a tool its own
# skill forbids, whose only possible outcome was a refusal at the moment of real work.
#
# The chat scopes (`general`, `onboarding`, `diagnosis`) are deliberately excluded: a human is at
# the keyboard approving each call there, so the policy allowlist — which exists to decide what may
# proceed WITHOUT one — is not the gate that applies.
BACKGROUND_SCOPES = (*PHASES, "itemize", "deputy", "handoff", "resolve")

# Phases whose report is still written by the agent from a path named in prose. EMPTY, and that is
# the point: every reported phase now routes its write through a pen that owns the path. The set
# stays because the ratchet is the value — a phase added here is a regression, and this suite fails
# rather than letting the class quietly return.
NO_PEN_YET: set[str] = set()


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def skill_text(phase: str) -> str:
    p = SKILLS / phase / "SKILL.md"
    return p.read_text() if p.is_file() else ""


def tools_named_by(phase: str) -> set[str]:
    """Registered tool names this skill mentions. Intersecting with the catalogue is what keeps
    this precise: a skill's prose is full of backticked words, and only the ones that are real
    tools are claims about what the run can do."""
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
    """The inverse of the check above, and the one that catches a tool nobody's prose mentions.

    Giving a background run a tool the policy forbids is worse than not giving it one: the model
    sees it in its tool list, reaches for it at the moment it is needed, and is refused with nobody
    to appeal to. Whatever that tool was for silently does not happen. So a scope with no human
    behind it may mount ONLY tools that can actually proceed."""
    for scope in BACKGROUND_SCOPES:
        mounted = set(TOOL_SCOPES.get(scope, ()))
        refused = sorted(t for t in mounted if not is_safe(f"mcp__dev__{t}", {}))
        ok(f"{scope}: every tool it mounts can actually proceed unattended — refused {refused}",
           not refused)


def test_every_report_the_reader_asks_for_has_a_writer():
    """The reader looks in exactly one place. Whoever writes the report has to agree with it, and
    the only way to guarantee that is for code to build the path."""
    src = (ROOT / "superme_agent/harness/tools/dev_tools.py").read_text()
    penned, prose = set(), set()
    for phase in REPORTED:
        if f"file_{phase}_report" in src:
            penned.add(phase)
        if f"reports/report-{phase}.md" in skill_text(phase):
            prose.add(phase)

    ok(f"the phases with a pen own their path in code — {sorted(penned)}",
       penned == set(REPORTED) - NO_PEN_YET)
    # Naming the path is fine — a skill may say what the pen produces. What matters is that the
    # skill routes the agent THROUGH the pen, so the path is never the agent's to resolve.
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
