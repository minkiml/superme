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


def _run(cmd: str, worktree: Path) -> tuple[int, str] | None:
    """One command in the sandbox at the worktree → (exit code, output tail). None when this host
    has no supported sandbox — the caller decides what an unrunnable command means."""
    argv = kernel_command(cmd, [worktree])
    if argv is None:
        return None
    try:
        p = subprocess.run(argv, cwd=str(worktree), capture_output=True, text=True,
                           timeout=CHECK_TIMEOUT_S)
        code, out = p.returncode, ((p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired:
        code, out = 124, f"timed out after {CHECK_TIMEOUT_S}s"
    except OSError as e:                      # the command could not be launched at all
        code, out = 127, str(e)
    return code, out[-_TAIL:].strip()


def dry_run(item_dir: Path, repo_dir: Path) -> list[dict]:
    """Execute the `run:` blocks the PLAN has already written, and record NOTHING.

    A planner authoring a check has no way to find out that the command it just wrote does not
    execute — and a broken one is invisible until vet spends a whole cycle discovering it. Live on
    2026-08-07 twice over: one plan's `run:` pointed at the wrong checkout, another put `--quiet`
    where argparse rejects it, and both cost a build⟷vet round to learn what an exit code says in
    a second. The plan phase asked to try its own commands and was denied, because a shell it can
    run arbitrary things in is not something it should have.

    So this is not a shell. It runs ONLY the strings already committed to plan.md, and it writes no
    verdict anywhere: at plan time the feature does not exist, so an honest failure is the expected
    answer and recording it would be a lie about work nobody has done. What the planner is looking
    for is the OTHER kind of red — a usage error, an import error, a command that cannot start —
    which is the difference between "not built yet" and "this check will never run".

    Returns [{check, code, result}] — one row per runnable check, in plan order."""
    rows: list[dict] = []
    for c in runnable_checks(item_dir):
        got = _run(c["run"], repo_dir)
        if got is None:
            log.warning("kernel dry-run unavailable on this host")
            return []
        code, tail = got
        rows.append({"check": c["id"], "code": code,
                     "result": f"exit {code}" + (f" · {tail}" if tail else "")})
    return rows


def audit_validation(item_dir: Path, worktree: Path, *, cycle: int | None = None) -> list[dict]:
    """Re-run the commands BUILD recorded this cycle and compare the machine to build's claim.

    Validation is build's self-check and stays build's to run (design §Further Notes). What it
    cannot be is build's alone to WITNESS: a green nobody re-derived is a sentence, and the loop
    would advance on it. So the kernel re-executes each recorded command once, on the tree as it
    stands now, and a disagreement becomes a finding the loop hands back to build — inside the
    autonomous build⟷vet loop, with no human in it and no unit test entering vet's own exam.

    Each DISTINCT command is audited once, against build's LAST claim for it: a command build ran
    red, fixed, and ran green again is one claim — the green — not two.

    Returns [{command, claimed, actual, agrees, result}]. `[]` when build recorded nothing (the
    caller reads that as its own kind of finding) or when the host cannot run anything, which must
    never read as a disagreement: an audit that could not run has learned nothing."""
    claims: dict[str, dict] = {}
    for r in _arts.validation_runs(Path(item_dir), cycle=cycle):
        claims[r["command"]] = r
    rows: list[dict] = []
    for cmd, claim in claims.items():
        got = _run(cmd, worktree)
        if got is None:
            log.warning("kernel audit unavailable on this host — build's claims stay unaudited")
            return []
        code, tail = got
        actual = code == 0
        result = f"exit {code}" + (f" · {tail}" if tail else "")
        try:
            _arts.record_validation_audit(Path(item_dir), worktree, command=cmd,
                                          claimed=bool(claim["passed"]), actual=actual,
                                          result=result)
        except (ValueError, OSError):
            log.exception("validation audit ran for %r but could not be recorded", cmd)
        rows.append({"command": cmd, "claimed": bool(claim["passed"]), "actual": actual,
                     "agrees": bool(claim["passed"]) == actual, "result": result})
    return rows


def runnable_checks(item_dir: Path, *, skip: list[str] | None = None) -> list[dict]:
    """The plan's checks carrying a literal `run:` block, minus `skip` (the build's deferrals — a
    check awaiting the owner's authorization is not for us to run and never converges if we do)."""
    plan = Path(item_dir) / "artifacts" / _arts.artifact_file("plan")
    if not plan.is_file():
        return []
    blocked = set(skip or ())
    # A check carrying a RUBRIC is never ours, even with a `run:` block: its verdict includes a
    # judgment, and a machine entry is final — recording the exit code here would lock out the
    # criteria half. The vetter runs the command itself and records both together.
    return [c for c in _arts.parse_vet_plan(plan.read_text()).get("checks", [])
            if c.get("run") and c.get("id") and c["id"] not in blocked and not c.get("rubric")]


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
