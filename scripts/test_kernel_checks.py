"""Kernel-executed verification checks.

A check that is one command has no judgment in it, so the kernel runs it and writes the verdict
itself. Real commands run here, since a mocked subprocess would pin nothing.

Run: PYTHONPATH=. python -m scripts.test_kernel_checks
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from superme_agent.core import artifacts as _arts
from superme_agent.core.kernel_speech import vet_trigger
from superme_agent.core.vocab.sandbox import kernel_command
from superme_agent.daemon.services import checks as _checks
from scripts.sources import src

PASS = 0
ROOT = Path(__file__).resolve().parents[1]
SANDBOXED = sys.platform == "darwin" and shutil.which("sandbox-exec")


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


PLAN = """# Plan — probe

## Verification plan
depth: checks
reason: probing the executor
env: none

### suite-green
- traces: PRD deliverable d-1
- covers: t1
- mode: command
- scenario: run the suite
- run: test -f built.txt
- expect: the marker file the build was supposed to write exists

### tally-adds
- traces: PRD deliverable d-1
- covers: t1
- mode: command
- scenario: add an expense
- run: exit 3
- expect: the row lands with today's date

### looks-right
- traces: user story u-2
- covers: t2
- mode: inspection
- scenario: read the rendered card
- expect: the card reads as a sentence, not a field dump
"""


def _item() -> tuple[Path, Path]:
    """A work-item folder with a plan and one cycle report, plus a worktree to run in."""
    root = Path(tempfile.mkdtemp(prefix="kchk-"))
    item, wt = root / "item", root / "wt"
    (item / "artifacts").mkdir(parents=True)
    wt.mkdir()
    (item / "artifacts" / _arts.artifact_file("plan")).write_text(PLAN, encoding="utf-8")
    _arts.scaffold_cycle(item, title="probe")
    return item, wt


# ── the contract ────────────────────────────────────────────────────────────────────────────────

def test_run_is_a_check_field():
    vp = _arts.parse_vet_plan(PLAN)
    by_id = {c["id"]: c for c in vp["checks"]}
    ok("a check may carry a literal run block", by_id["suite-green"]["run"] == "test -f built.txt")
    ok("a check without one parses with run empty — it is optional",
       by_id["looks-right"]["run"] == "")
    ok("no run block is not a gate issue — plan is never blocked by an unlinearisable scenario",
       not [i for i in _arts.vet_plan_hard_issues(vp) if "run" in i])


def test_only_runnable_undeferred_checks_are_ours():
    item, _ = _item()
    ids = [c["id"] for c in _checks.runnable_checks(item)]
    ok("the inspection check is left to the agent", ids == ["suite-green", "tally-adds"])
    ids = [c["id"] for c in _checks.runnable_checks(item, skip=["tally-adds"])]
    ok("a check awaiting the owner's authorization is never executed", ids == ["suite-green"])


# ── the kernel's sandbox ────────────────────────────────────────────────────────────────────────

def test_kernel_sandbox_holds():
    if not SANDBOXED:
        ok("host has no supported sandbox — kernel execution correctly declines",
           kernel_command("true", []) is None)
        return
    inside = Path(tempfile.mkdtemp(prefix="kbox-"))
    # NOT under the per-user temp dir, which is writable on purpose: shared /tmp is the real out-
    # of-boundary target.
    outside = Path(tempfile.mkdtemp(prefix="kbox-out-", dir="/private/tmp"))

    def run(cmd: str) -> int:
        return subprocess.run(kernel_command(cmd, [inside]), cwd=str(inside),
                              capture_output=True, text=True, timeout=60, encoding="utf-8").returncode

    ok("a write inside the declared root succeeds", run(f"echo hi > {inside}/a.txt") == 0)
    ok("a write outside it is refused by the kernel", run(f"echo hi > {outside}/a.txt") != 0)
    ok("…and really did not happen", not (outside / "a.txt").exists())
    ok("outbound network is refused",
       run("curl -sS -m 8 -o /dev/null https://example.com") != 0)
    # The class of check most worth having: one that drives the real thing on a local port.
    ok("binding a localhost port still works",
       run('python3 -c "import socket;s=socket.socket();s.bind((\'127.0.0.1\',0))"') == 0)


# ── execution and the record ────────────────────────────────────────────────────────────────────

def test_exit_code_decides_and_the_kernel_records():
    if not SANDBOXED:
        print("  ..  skipped (no sandbox on this host)")
        return
    item, wt = _item()
    (wt / "built.txt").write_text("built\n", encoding="utf-8")          # makes suite-green pass, tally-adds still exits 3
    rows = _checks.execute(item, wt, title="probe")
    by = {r["check"]: r for r in rows}
    ok("both runnable checks ran", set(by) == {"suite-green", "tally-adds"})
    ok("exit 0 passes", by["suite-green"]["passed"] is True)
    ok("a non-zero exit fails — no interpretation step", by["tally-adds"]["passed"] is False)
    ok("…and the exit code is kept as the evidence", by["tally-adds"]["code"] == 3)

    entries = {e["check"]: e for e in _arts.evidence_entries(item)}
    ok("the kernel wrote the ledger itself", set(entries) == {"suite-green", "tally-adds"})
    ok("entries are stamped machine", all(e["by"] == "machine" for e in entries.values()))
    ok("the command it ran is recorded verbatim", entries["suite-green"]["how"] == "test -f built.txt")
    ok("a failure carries expected-vs-actual", "got 3" in entries["tally-adds"].get("note", ""))
    ok("the unrunnable check is untouched — it is the agent's", "looks-right" not in entries)


def test_a_machine_entry_is_final():
    if not SANDBOXED:
        print("  ..  skipped (no sandbox on this host)")
        return
    item, wt = _item()
    _checks.execute(item, wt, title="probe")
    try:
        _arts.record_verification(item, wt, check="tally-adds", how="I re-ran it",
                                  result="looked fine to me", passed=True)
        ok("an agent cannot overwrite a kernel verdict", False)
    except ValueError as e:
        ok("an agent cannot overwrite a kernel verdict", "cannot be re-recorded" in str(e))
    # …and the agent's own checks still record normally, or the guard would have eaten the phase.
    e = _arts.record_verification(item, wt, check="looks-right", how="read the card",
                                  result="reads as a sentence", passed=True)
    ok("an agent-performed check still records", e["by"] == "agent")


def test_the_vetter_is_told_what_is_already_done():
    t = vet_trigger("i1", "T", machine=[{"check": "suite-green", "passed": True, "result": "exit 0"},
                                        {"check": "tally-adds", "passed": False, "result": "exit 3"}])
    ok("the trigger names each kernel-run check", "`suite-green`" in t and "`tally-adds`" in t)
    ok("…with its verdict", "PASS" in t and "FAIL" in t)
    ok("…and tells the vetter not to redo them", "Do not re-run or re-record" in t)
    ok("a cycle with none of them says nothing about it",
       "kernel already ran" not in vet_trigger("i1", "T"))


# ── wiring ──────────────────────────────────────────────────────────────────────────────────────

def test_the_loop_runs_them_before_the_session():
    loop_src = src("superme_agent/daemon/services/loop.py")
    vet = loop_src.split("async def _run_background_vet")[1].split("async def ")[0]
    ok("vet's runner executes the kernel checks", "_checks.execute" in vet)
    ok("…off the event loop, since a suite is not instant", "asyncio.to_thread" in vet)
    ok("…before the trigger is built",
       vet.index("_checks.execute") < vet.index("kernel_speech.vet_trigger"))
    ok("…excluding the build's deferrals", "skip=deferred" in vet)
    ok("…and a broken executor never fails the run", "vet proceeds and performs them itself" in vet)

    ok("the surface can tell the two classes apart",
       "by: str" in src("superme_agent/daemon/schemas/dev/gates.py")
       and "machine-run" in src("web/frontend/src/features/dev/WorkItemModal.tsx"))

    vet_skill = " ".join(src("superme_agent/harness/plugins/superme-dev/skills/vet/SKILL.md").split())
    ok("the vet skill says kernel-run checks are already done, and not to redo them",
       "kernel already executed" in vet_skill.lower()
       and "refuses a second entry" in vet_skill.lower())
    plan_skill = src("superme_agent/harness/plugins/superme-dev/skills/plan/SKILL.md")
    ok("the plan skill teaches when to write a run block, and when not to",
       "run:" in plan_skill and "judge it" in plan_skill)
    tmpl = (ROOT / "superme_agent/harness/plugins/superme-dev/skills/plan/templates/"
                   "plan-template.md").read_text(encoding="utf-8")
    ok("the template offers the field as optional", "- run: <fill:optional" in tmpl)


def main() -> None:
    test_run_is_a_check_field()
    test_only_runnable_undeferred_checks_are_ours()
    test_kernel_sandbox_holds()
    test_exit_code_decides_and_the_kernel_records()
    test_a_machine_entry_is_final()
    test_the_vetter_is_told_what_is_already_done()
    test_the_loop_runs_them_before_the_session()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
