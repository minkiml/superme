"""The kernel executes what can be executed (verification-model design §4).

A verification-plan check may carry a literal `run:` command. When it does, the daemon runs it —
in the sandbox, in the item's worktree, before the vet session opens — and writes the verdict to
the evidence ledger itself. Vet then reads that result as a fact of the world, exactly as it reads
the diff, and spends its judgment on the checks that actually need judging.

Two things this buys, in order of how much they matter:

**Freshness.** A runnable exam re-runs for free on every cycle. Evidence stops expiring into a
fingerprint mismatch that costs a whole vet session to re-establish — which is the tax the loop was
quietly paying every time build touched a file.

**Integrity.** A machine entry cannot be invented, skipped, or summarised away, because no model is
between the exit code and the record. That is a real property, but it is the second reason: the
vetter was not suspected of lying, it was suspected of being expensive.

Privileged, not mandated. A check without a `run:` block is performed by the vet agent and marked
agent-attested; both classes are legitimate evidence and both are visible as what they are. Plan is
never blocked by a scenario it cannot linearise.

If the host has no supported sandbox, nothing runs here. Falling back to an unisolated execution
would put the strongest-looking entries in the ledger behind the weakest guarantee, which is worse
than leaving the check to the agent.
"""

import logging
import subprocess
from pathlib import Path

from ...core import artifacts as _arts
from ...core.sandbox import kernel_command

log = logging.getLogger("superme-agent")

# A single check's wall clock. Generous enough for a real suite, short enough that one hung command
# cannot hold a vet cycle open forever — the timeout IS a failure, with the elapsed time as evidence.
CHECK_TIMEOUT_S = 600

# How much of a command's output the ledger keeps. The whole point of a machine entry is the raw
# result rather than a summary, but a 10 MB test log in a markdown fence helps nobody.
_TAIL = 1200


def runnable_checks(item_dir: Path, *, skip: list[str] | None = None) -> list[dict]:
    """The plan's checks carrying a literal `run:` block, minus `skip` (the build's deferrals — a
    check awaiting the owner's authorization is not for us to run and never converges if we do)."""
    plan = Path(item_dir) / "artifacts" / _arts.artifact_file("plan")
    if not plan.is_file():
        return []
    blocked = set(skip or ())
    return [c for c in _arts.parse_vet_plan(plan.read_text()).get("checks", [])
            if c.get("run") and c.get("id") and c["id"] not in blocked]


def execute(item_dir: Path, worktree: Path, *, skip: list[str] | None = None,
            title: str = "") -> list[dict]:
    """Run every runnable check and record each verdict. Returns one row per check
    [{check, passed, code, result}] — for the trigger that tells vet what is already decided.

    Exit status decides: 0 passes, anything else fails. There is no interpretation step, which is
    the entire point; a check whose pass condition needs interpreting should not carry a `run:`.
    """
    checks = runnable_checks(item_dir, skip=skip)
    if not checks:
        return []
    rows: list[dict] = []
    for c in checks:
        cid, cmd = c["id"], c["run"]
        argv = kernel_command(cmd, [worktree])
        if argv is None:
            log.warning("kernel checks unavailable on this host — %s stays agent-attested", cid)
            return []          # all-or-nothing: a half-executed exam is the worst of both
        try:
            p = subprocess.run(argv, cwd=str(worktree), capture_output=True, text=True,
                               timeout=CHECK_TIMEOUT_S)
            code, out = p.returncode, ((p.stdout or "") + (p.stderr or ""))
        except subprocess.TimeoutExpired:
            code, out = 124, f"timed out after {CHECK_TIMEOUT_S}s"
        except OSError as e:                      # the command could not be launched at all
            code, out = 127, str(e)
        tail = out[-_TAIL:].strip()
        passed = code == 0
        result = f"exit {code}" + (f" · {tail}" if tail else "")
        try:
            _arts.record_verification(
                Path(item_dir), worktree, check=cid, how=cmd, result=result, passed=passed,
                note="" if passed else f"expected exit 0, got {code}",
                title=title, by=_arts.BY_MACHINE)
        except ValueError:
            # The ledger refused it — a plan whose check ids moved under us, or `depth: none`. The
            # run happened; the record is the ledger's call, and vet still sees the row below.
            log.exception("kernel check %s ran but could not be recorded", cid)
        rows.append({"check": cid, "passed": passed, "code": code, "result": result})
    return rows
