"""The kernel executes what can be executed.

A check may carry a literal `run:` command; the daemon runs it in the sandbox before the vet
session opens, and writes the verdict to the ledger. A check without one is agent-attested.
"""

import logging
import subprocess
from pathlib import Path

from ...core import artifacts as _arts
from ...core.vocab.sandbox import kernel_command

log = logging.getLogger("superme-agent")

# Generous enough for a real suite, short enough that one hung command cannot hold a vet cycle
# open.
CHECK_TIMEOUT_S = 600

# The raw result is the point of a machine entry, but a 10 MB log in a fence helps nobody.
_TAIL = 1200


def _run(cmd: str, worktree: Path) -> tuple[int, str] | None:
    """One command in the sandbox at the worktree → (exit code, output tail). None when this host has no
    supported sandbox."""
    argv = kernel_command(cmd, [worktree])
    if argv is None:
        return None
    try:
        p = subprocess.run(argv, cwd=str(worktree), capture_output=True, text=True,
                           timeout=CHECK_TIMEOUT_S, encoding="utf-8")
        code, out = p.returncode, ((p.stdout or "") + (p.stderr or ""))
    except subprocess.TimeoutExpired:
        code, out = 124, f"timed out after {CHECK_TIMEOUT_S}s"
    except OSError as e:                      # the command could not be launched at all
        code, out = 127, str(e)
    return code, out[-_TAIL:].strip()


def dry_run(item_dir: Path, repo_dir: Path) -> list[dict]:
    """Execute the `run:` blocks the PLAN has already written, and record nothing.

    Not a shell: the planner is looking for a command that cannot start at all."""
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
    """Re-run the commands BUILD recorded and compare the machine to its claim.

    Validation is build's to run but not build's alone to WITNESS."""
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
    """The plan's checks carrying a literal `run:` block, minus `skip` — a check awaiting the owner's
    authorization is not ours to run and never converges if we do."""
    plan = Path(item_dir) / "artifacts" / _arts.artifact_file("plan")
    if not plan.is_file():
        return []
    blocked = set(skip or ())
    # A check carrying a RUBRIC is never ours: its verdict includes a judgment, and a machine
    # entry is final.
    return [c for c in _arts.parse_vet_plan(plan.read_text(encoding="utf-8")).get("checks", [])
            if c.get("run") and c.get("id") and c["id"] not in blocked and not c.get("rubric")]


def execute(item_dir: Path, worktree: Path, *, skip: list[str] | None = None,
            title: str = "") -> list[dict]:
    """Run every runnable check and record each verdict, one row per check.

    Exit status decides: a check whose pass condition needs interpreting should not carry a `run:`."""
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
                               timeout=CHECK_TIMEOUT_S, encoding="utf-8")
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
            # The ledger refused it — the run happened, and vet still sees the row below.
            log.exception("kernel check %s ran but could not be recorded", cid)
        rows.append({"check": cid, "passed": passed, "code": code, "result": result})
    return rows
