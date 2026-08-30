"""A kernel-fired run is ONE turn, and a phase may not end owing its own gate.

Two failures found in the 2026-08-28 live E2E. Both cost a run that produced nothing: a subagent
backgrounded into a turn that never came, and a plan that stopped holding the fix its gate wanted.

Run: PYTHONPATH=. python scripts/test_single_turn.py
"""

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superme_agent.core.permissions import single_turn_hook            # noqa: E402
from superme_agent.harness.tools.run_tools import _report_completion   # noqa: E402

PASS = 0


def ok(label: str, cond, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {label} {detail}"
    PASS += 1
    print(f"  ok - {label}")


def decide(fn, tool: str, args: dict):
    class Ctx:
        pass
    return asyncio.run(fn(tool, args, Ctx()))


def name(r) -> str:
    return type(r).__name__


async def _yes(_t, _a):
    return True


def hook_says(tool: str, args: dict | None = None):
    """What the PreToolUse hook decides. A HOOK and not `can_use_tool`, because the SDK does not
    consult the permission callback for `Agent` or its own control tools — measured live, a
    backgrounded spawn and a ScheduleWakeup both sailed past the callback version."""
    out = asyncio.run(single_turn_hook()(
        {"tool_name": tool, "tool_input": args or {}}, None, None))
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision"), \
           (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")


def test_no_later_turn() -> None:
    print("a kernel-fired run has no turn after this one")

    # Verbatim the shape that killed run 2330: Agent defaults to background, so its result
    # lands in a turn that never comes.
    d, why = hook_says("Agent", {"prompt": "explore", "subagent_type": "Explore"})
    ok("a backgrounded subagent is denied", d == "deny")
    ok("...and the denial names the fix", "run_in_background: false" in why)
    d, _ = hook_says("Agent", {"prompt": "explore", "run_in_background": False})
    ok("a FOREGROUND subagent passes", d is None)
    d, why = hook_says("ScheduleWakeup", {"delaySeconds": 60})
    ok("ScheduleWakeup is denied", d == "deny")
    ok("...and says the awaited thing will never arrive", "never arrive" in why)
    for t in ("CronCreate", "Monitor"):
        ok(f"{t} is denied too", hook_says(t)[0] == "deny")
    ok("an ordinary tool is untouched", hook_says("Read", {"file_path": "x"})[0] is None)


def test_hook_is_mounted_by_default() -> None:
    """The composition, not the hook — a runner that forgets is the failure this guards."""
    print("\nmounted by DEFAULT, and only the interactive surface opts out")
    from superme_agent.core.agent_service import _with_single_turn_hook
    ok("a kernel run gets the hook",
       len((_with_single_turn_hook(None, False) or {}).get("PreToolUse", [])) == 1)
    ok("a chat surface does not",
       len((_with_single_turn_hook(None, True) or {}).get("PreToolUse", [])) == 0)
    existing = {"PreToolUse": ["already here"]}
    ok("an existing hook is kept, not replaced",
       len(_with_single_turn_hook(existing, False)["PreToolUse"]) == 2)


def call(handler, outcome="success"):
    return asyncio.run(handler({
        "machine": {"outcome": outcome},
        "user": {"summary": "did the thing", "next": "your review"}}))


def test_exit_check() -> None:
    print("\na phase may not declare done while its own gate would bounce it")
    owed: list[str] = []
    h = _report_completion(completion_sink={}, exit_check=lambda: owed)

    r = call(h)
    ok("a clean phase reports normally", not r.get("is_error"))

    owed[:] = ["plan.md is missing", "check `full_suite` is not a lowercase slug"]
    r = call(h)
    txt = r["content"][0]["text"]
    ok("a phase owing its gate is REFUSED", r.get("is_error"))
    ok("...and is told exactly what it owes", "plan.md is missing" in txt and "lowercase slug" in txt)
    ok("...and why it is cheaper to fix now", "send-back" in txt and "re-run" in txt)

    # A wall is not a claim of completeness. Refusing these would trap a run with nowhere to go.
    for outcome in ("needs_user", "blocked"):
        r = asyncio.run(h({"machine": {"outcome": outcome},
                           "user": {"summary": "hit a wall", "next": "your call",
                                    **({"questions": [{"question": "which way?", "recommend": "left",
                                                       "why": "shorter"}]}
                                       if outcome == "needs_user" else {})}}))
        ok(f"`{outcome}` still gets out — it REPORTS the wall, it does not claim done",
           not r.get("is_error"))

    # CONTROL: no exit_check (every phase but plan) behaves exactly as before.
    h2 = _report_completion(completion_sink={})
    ok("control: a phase with no exit check is unaffected", not call(h2).get("is_error"))
    # A check that throws must not turn a finished run into a failure.
    h3 = _report_completion(completion_sink={}, exit_check=lambda: 1 / 0)
    ok("a broken check never blocks the ending", not call(h3).get("is_error"))


def test_wired_to_plan_only() -> None:
    print("\nwired where the gate is mechanical, and nowhere else")
    from superme_agent.daemon.services.runs.background import phase_exit_check
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        ok("plan gets a check", phase_exit_check("plan", d, "implementation") is not None)
        for skill in ("triage", "build", "vet", "review", "close", "investigate"):
            ok(f"{skill} does not", phase_exit_check(skill, d, "implementation") is None)
        issues = phase_exit_check("plan", d, "implementation")()
        ok("an item with no plan.md at all is caught", bool(issues), issues)


test_no_later_turn()
test_hook_is_mounted_by_default()
test_exit_check()
test_wired_to_plan_only()
print(f"\nALL GREEN — {PASS} checks passed.")
