"""The build-vet loop driver and its breakers.

Every branch of the pure decision `decide_after_vet`, plus the invariant no branch may break:
none of them parks inside the loop. Convergence is counted by appearance, not by equality.

Run: PYTHONPATH=. python -m scripts.test_bv_s5
"""

import asyncio
import inspect as _inspect
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from superme_agent.core import artifacts as A
from superme_agent.core.spine import SystemSpine
from superme_agent.core.vocab.token_taxonomy import category_for
from superme_agent.daemon.services import loop as L
from superme_agent.daemon.services.loop import decide_after_vet

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def make_repo(tmp: Path) -> Path:
    repo = tmp / "repo" if tmp.name != "fps" else tmp
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def make_item_dir(tmp: Path, name: str) -> Path:
    d = tmp / name
    (d / "artifacts").mkdir(parents=True)
    return d


ITEM = {"id": "i1", "status": "active", "phase": "vet"}


def _d(**kw) -> dict:
    """decide_after_vet with test defaults; override per-case."""
    args = dict(item=dict(ITEM), evidence={"status": "passed"}, fingerprint="", attempts=[],
                spent=0, budget=100, turn_error=False)
    args.update(kw)
    return decide_after_vet(args.pop("item"), **args)


def _lenses(d) -> None:
    """The three standing lenses, owed on every cycle before the report will write
    Not what this suite is testing — just the bar it now has to clear."""
    for ln in A.STANDING_LENSES:
        A.record_lens(d, lens=ln, probed="read the diff through this lens")

def test_attempts_ledger(tmp: Path) -> None:
    print("`## Cycle outcome` — the driver's own record")
    d = make_item_dir(tmp, "item-att")
    ok("empty trail reads back empty", A.read_cycle_outcomes(d) == [])
    ok("no cycle report → outcome is a recorded no-op",
       A.append_cycle_outcome(d, evidence="failed", decision="build", reason="r") is None)
    A.scaffold_cycle(d, title="t")
    A.append_cycle_outcome(d, evidence="failed", decision="build",
                           reason="2 checks\nfailed", fingerprint="abc123",
                           failed=["a-check", "b-check"], tokens=1000, budget=5000)
    A.scaffold_cycle(d, title="t")   # outcome closed cycle 1 → opens cycle 2
    A.append_cycle_outcome(d, evidence="passed", decision="review", reason="all green")
    rows = A.read_cycle_outcomes(d)
    ok("entries parse back in order (cycle from the file)",
       len(rows) == 2 and rows[0]["cycle"] == 1 and rows[1]["cycle"] == 2
       and rows[1]["decision"] == "review")
    ok("fields land (one-line coerced)",
       rows[0]["reason"] == "2 checks failed" and rows[0]["fingerprint"] == "abc123"
       and rows[0]["failed"] == "a-check, b-check" and rows[0]["tokens"] == "1000 / 5000")
    ok("passing entries carry no fingerprint", "fingerprint" not in rows[1])


def test_fingerprint(tmp: Path, repo: Path) -> None:
    print("convergence fingerprint")
    d = make_item_dir(tmp, "item-fp")
    ok("no evidence → empty fingerprint (never trips the guard)",
       A.convergence_fingerprint(d) == "")
    A.record_verification(d, repo, check="alpha", how="pytest", result="3 passed", passed=True)
    ok("all-green ledger → empty fingerprint", A.convergence_fingerprint(d) == "")
    A.record_verification(d, repo, check="beta", how="read",
                      result="FAILED at 2026-07-17T10:22:33 exit 1 addr 0x7f8a2b", passed=False)
    fp1 = A.convergence_fingerprint(d)
    ok("failing ledger → non-empty fingerprint", len(fp1) == 12)
    # Same failure, different incidental noise (new timestamp/address) → SAME fingerprint.
    A.record_verification(d, repo, check="beta", how="read",
                      result="failed at 2026-08-01T99:11:22 exit 1 addr 0xdeadbeef", passed=False)
    ok("incidental variation (timestamp/hex/case) does not move the fingerprint",
       A.convergence_fingerprint(d) == fp1)
    # A genuinely different failure → different fingerprint.
    A.record_verification(d, repo, check="beta", how="read",
                      result="exit 2: ImportError no module named probe", passed=False)
    ok("a real change in the failure moves the fingerprint",
       A.convergence_fingerprint(d) != fp1)
    # Fix beta, fail alpha → the failing SET changes the fingerprint too.
    fp2 = A.convergence_fingerprint(d)
    A.record_verification(d, repo, check="beta", how="read", result="ok now", passed=True)
    A.record_verification(d, repo, check="alpha", how="pytest", result="1 failed", passed=False)
    ok("the failing check-set is part of the fingerprint",
       A.convergence_fingerprint(d) not in ("", fp1, fp2))
    ok("single digits survive normalization (they're signal)",
       "exit 1" in A._normalize_signature("Exit 1! (code=0x1f) 12345"))


def test_fingerprint_scope(tmp: Path) -> None:
    print("fingerprint scope — tracked content only (2026-07-30)")
    repo = make_repo(tmp / "fps")
    fp0 = A.repo_fingerprint(repo)
    # A vet run's own litter is untracked, and counting it made vet stale its own evidence.
    (repo / ".coverage").write_text("junk", encoding="utf-8")
    (repo / "tmp.log").write_text("noise", encoding="utf-8")
    ok("untracked litter does NOT move the fingerprint", A.repo_fingerprint(repo) == fp0)
    # A tracked edit — build's actual implementation — still does. That is what stale-on-edit is for.
    (repo / "a.py").write_text("x = 2\n", encoding="utf-8")
    fp1 = A.repo_fingerprint(repo)
    ok("a tracked edit DOES move it", fp1 != fp0)
    (repo / "a.py").write_text("x = 3\n", encoding="utf-8")
    ok("...and keeps moving as the same dirty file changes (content, not a status summary)",
       A.repo_fingerprint(repo) not in (fp0, fp1))
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "c2"],
                   cwd=repo, check=True)
    ok("committing moves it too (HEAD is part of the key)", A.repo_fingerprint(repo) != fp0)
    ok("no repo → no-git", A.repo_fingerprint(None) == "no-git")


def test_no_progress_guard(tmp: Path, repo: Path) -> None:
    print("no-progress guard — an unchanged tree is never re-vetted")
    d = make_item_dir(tmp, "item-noprog")
    ok("no evidence yet → always vet (the opening cycle has nothing to compare)",
       L._tree_moved_since_evidence(d, repo) is True)
    A.record_verification(d, repo, check="a", how="pytest", result="ok", passed=True)
    ok("straight after recording, the tree has not moved",
       L._tree_moved_since_evidence(d, repo) is False)
    (repo / "untracked-litter.txt").write_text("from the test run", encoding="utf-8")
    ok("...and a test's own litter does not count as movement",
       L._tree_moved_since_evidence(d, repo) is False)
    (repo / "untracked-litter.txt").unlink()
    (repo / "a.py").write_text("x = 99\n", encoding="utf-8")
    ok("a real build edit counts as movement", L._tree_moved_since_evidence(d, repo) is True)
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")

    # The plan is not in the worktree, so a revision fixing a broken check must be vetted.
    print("no-progress guard — a revision since the last verdict always re-vets")
    ok("an unrevised plan reads as not-moved", L._plan_moved_since_evidence(d) is False)
    (d / "artifacts" / "plan.md").write_text(
        "# Plan\n\n## Revision r1 — 2026-08-07T10:00:00\n- scope: targeted\n\n## Tasks\n", encoding="utf-8")
    ok("a revision recorded after the last verdict forces a vet",
       L._plan_moved_since_evidence(d) is True)
    # Verify the new plan once, not never skip again.
    A.append_cycle_outcome(d, evidence="failed", decision="review", reason="handed over")
    A.scaffold_cycle(d, title="after the revision")
    A.record_verification(d, repo, check="a", how="pytest", result="ok", passed=True)
    ok("a verdict recorded under the revision settles it", L._plan_moved_since_evidence(d) is False)
    (d / "artifacts" / "plan.md").unlink()


def test_evidence_check_guard(tmp: Path, repo: Path) -> None:
    print("evidence check-id guard (B4 — ledger key == vet-plan id)")
    d = make_item_dir(tmp, "item-guard")
    plan = d / "artifacts" / A.artifact_file("plan")
    plan.write_text(
        "# Plan\n\n## Vet plan\ndepth: behavior\nreason: r\nenv: n/a\n\n"
        "### stats-top-n-ranked\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n"
        "### sum-csv-flag\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n", encoding="utf-8")
    # Exact id → records; and the ledger key is exactly that id.
    A.record_verification(d, repo, check="stats-top-n-ranked", how="pytest", result="ok", passed=True)
    ok("exact vet-plan id records", A.evidence_entries(d)[-1]["check"] == "stats-top-n-ranked")
    # Glued key (id + description) → REFUSED with a targeted hint naming the bare id.
    try:
        A.record_verification(d, repo, check="stats-top-n-ranked: leaderboard, no TOTAL",
                          how="read", result="bad", passed=False)
        ok("glued key refused", False, "should have raised")
    except ValueError as e:
        ok("glued key refused, hint names the bare id",
           "stats-top-n-ranked" in str(e) and "bare id" in str(e))
    # Unknown check (not in the plan) → REFUSED, lists the valid ids.
    try:
        A.record_verification(d, repo, check="totally-made-up", how="read", result="x", passed=False)
        ok("unknown check refused", False, "should have raised")
    except ValueError as e:
        ok("unknown check refused, lists valid ids",
           "not a vet-plan check id" in str(e) and "sum-csv-flag" in str(e))
    # The glued attempt left NO phantom key — the ledger still holds only the one clean entry.
    ok("no phantom key poisoned the ledger",
       [x["check"] for x in A.evidence_entries(d)] == ["stats-top-n-ranked"]
       and A.evidence_status(d, repo)["status"] in ("passed", "stale"))
    # No vet plan on disk → guard is inert (depth=none / non-vetted kinds record verbatim).
    d2 = make_item_dir(tmp, "item-noplan")
    A.record_verification(d2, repo, check="freeform label", how="read", result="ok", passed=True)
    ok("no vet plan → check recorded verbatim (guard inert)",
       A.evidence_entries(d2)[-1]["check"] == "freeform label")


def test_depth_none(tmp: Path, repo: Path) -> None:
    print("`depth: none` — an item that owes no checks passes cleanly (slice 5b)")
    d = make_item_dir(tmp, "item-nodepth")
    (d / "artifacts" / A.artifact_file("plan")).write_text(
        "# Plan\n\n## Tasks\n- [x] t1 — rename the constant\n\n## Verification plan\n"
        "depth: none\nreason: renames a constant, nothing observable changes\nenv: none\n", encoding="utf-8")
    ok("the plan's depth is readable from one place", A.plan_vet_depth(d) == "none")

    # An empty ledger reading as failure made the escape hatch the plan gate advertises a dead
    # end.
    ev = A.evidence_status(d, repo)
    ok("empty ledger under depth:none derives PASSED, not unverified",
       ev["status"] == "passed" and ev["entries"] == 0 and ev["not_required"] is True)
    ok("...so the loop driver advances it to review",
       _d(evidence=ev)["action"] == "review")

    # With no check ids a free-form entry would drive the loop off something vet invented.
    try:
        A.record_verification(d, repo, check="looks-fine", how="read", result="ok", passed=True)
        ok("recording under depth:none refused", False, "should have raised")
    except ValueError as e:
        ok("recording under depth:none is refused, and names the way to disagree",
           "depth: none" in str(e) and "revise" in str(e))

    # The KERNEL says so: an empty verification section is indistinguishable from a vet that gave
    # up.
    A.scaffold_cycle(d, title="t")
    p = A.note_no_verification(d)
    body = Path(p).read_text(encoding="utf-8")
    ok("the cycle report records the nothing-to-verify fact, quoting the plan's reason",
       "Nothing to verify" in body and "renames a constant" in body)
    ok("...and it is idempotent (a re-vet of the same cycle adds nothing)",
       A.note_no_verification(d) is None
       and body.count("Nothing to verify") == 1)

    # And the user-facing vet report renders instead of refusing.
    _lenses(d)
    r = A.write_vet_user_report(d, repo)
    rep = Path(r["path"]).read_text(encoding="utf-8")
    ok("report-vet.md renders the no-checks-owed verdict",
       "no checks were owed" in rep and "depth: none" in rep)

    # A real plan with checks is untouched by any of this.
    d2 = make_item_dir(tmp, "item-haschecks")
    (d2 / "artifacts" / A.artifact_file("plan")).write_text(
        "# Plan\n\n## Verification plan\ndepth: checks\nreason: r\nenv: none\n\n"
        "### a-check\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n", encoding="utf-8")
    ok("depth:checks with an empty ledger is still UNVERIFIED",
       A.evidence_status(d2, repo)["status"] == "unverified")


def test_evidence_orphan_scope(tmp: Path, repo: Path) -> None:
    print("evidence orphan scoping — a renamed/dropped check's stale FAIL can't pin the loop red")
    d = make_item_dir(tmp, "item-orphan")
    plan = d / "artifacts" / A.artifact_file("plan")
    two = ("# Plan\n\n## Vet plan\ndepth: checks\nreason: r\nenv: none\n\n"
           "### old-check\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n"
           "### keep-check\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n")
    plan.write_text(two, encoding="utf-8")
    A.record_verification(d, repo, check="old-check", how="run", result="bad", passed=False)
    A.record_verification(d, repo, check="keep-check", how="run", result="ok", passed=True)
    ok("both checks on the plan → failed on old-check", A.evidence_status(d, repo)["status"] == "failed")
    # A re-plan (or a build re-pointing its checks) drops old-check; its FAIL is now an ORPHAN.
    plan.write_text("# Plan\n\n## Vet plan\ndepth: checks\nreason: r\nenv: none\n\n"
                    "### keep-check\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n", encoding="utf-8")
    ev = A.evidence_status(d, repo)
    ok("orphaned check no longer pins the verdict red (scoped to the current plan)",
       ev["status"] in ("passed", "stale") and ev.get("orphaned") == ["old-check"])
    ok("unscoped view still sees the orphan (raw ledger, for audit)",
       A.evidence_status(d, repo, scope_to_plan=False)["status"] == "failed")
    # A plan whose checks have NO recorded evidence → unverified, never a false pass.
    plan.write_text("# Plan\n\n## Vet plan\ndepth: checks\nreason: r\nenv: none\n\n"
                    "### brand-new\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n", encoding="utf-8")
    ok("no current check has evidence → unverified (not a false pass)",
       A.evidence_status(d, repo)["status"] == "unverified")


def test_deferred_authorization(tmp: Path, repo: Path) -> None:
    print("BV-A2.1 — deferred check state via the authorization ledger")
    from superme_agent.daemon.services.loop import decide_after_build, decide_after_vet
    d = make_item_dir(tmp, "item-auth")
    plan = d / "artifacts" / A.artifact_file("plan")
    plan.write_text("# P\n\n## Vet plan\ndepth: checks\nreason: r\nenv: none\n\n"
                    "### reserved-edit\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n"
                    "### normal\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n", encoding="utf-8")
    A.record_verification(d, repo, check="normal", how="run", result="ok", passed=True)
    A.record_verification(d, repo, check="reserved-edit", how="run", result="cant", passed=False)
    ok("without a request the wall is a plain FAIL", A.evidence_status(d, repo)["status"] == "failed")
    # The build defers: an authorization request naming the blocked check.
    au = A.record_authorization(d, what="retire the legacy spec.md", why="reserved doc",
                                doc="spec", scope="doc-delete", check="reserved-edit")
    ev = A.evidence_status(d, repo)
    ok("a pending authorization DEFERS its check (not failed)",
       ev["status"] == "deferred" and ev["deferred_checks"] == ["reserved-edit"])
    ok("scope validation refuses an unknown scope",
       _raises(lambda: A.record_authorization(d, what="x", why="y", doc="z", scope="bogus")))
    # The loop advances a deferred ledger to REVIEW (never fail-closed).
    dec = decide_after_vet({"id": "i", "status": "active", "phase": "vet"}, evidence=ev,
                           fingerprint="", attempts=[], spent=0, budget=100)
    ok("deferred → advance to review (not halt)",
       dec["action"] == "review" and dec["deferred"] == ["reserved-edit"])
    # Close is refused while any request is pending (evidence isn't `passed`).
    ok("close gate: a pending authorization is not passed → refused",
       A.evidence_status(d, repo)["status"] != "passed" and len(A.pending_authorizations(d)) == 1)
    # Grant clears the pending flag; the check reverts to needing the work (build must still do it).
    upd = A.resolve_authorization(d, au["id"], decision="granted", by="owner")
    ok("grant marks the request granted + records who",
       upd["status"] == "granted" and upd["by"] == "owner" and not A.pending_authorizations(d))
    ok("after grant the deferral lifts — the un-done work is a real FAIL again (rebuild needed)",
       A.evidence_status(d, repo)["status"] == "failed")
    # A build that requested an authorization still ADVANCES toward vet — never pages.
    bd = decide_after_build({"id": "i", "status": "active", "phase": "build"},
                            outcome="partial", turn_error=False)
    ok("build with a deferral reports partial → advances toward vet", bd["stopping"] is False)

    # DENY path: a denied request WAIVES its check so the item can close with the gap on record.
    d2 = make_item_dir(tmp, "item-deny")
    plan2 = d2 / "artifacts" / A.artifact_file("plan")
    plan2.write_text("# P\n\n## Vet plan\ndepth: checks\nreason: r\nenv: none\n\n"
                     "### walled\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n"
                     "### fine\n- traces: t\n- mode: command\n- scenario: s\n- expect: e\n", encoding="utf-8")
    A.record_verification(d2, repo, check="fine", how="run", result="ok", passed=True)
    A.record_verification(d2, repo, check="walled", how="run", result="cant", passed=False)
    au2 = A.record_authorization(d2, what="rescope a deliverable", why="reserved", doc="roadmap",
                                 scope="roadmap-scope", check="walled")
    ok("pending → deferred", A.evidence_status(d2, repo)["status"] == "deferred")
    A.resolve_authorization(d2, au2["id"], decision="denied", by="owner")
    ev2 = A.evidence_status(d2, repo)
    ok("DENY waives the walled check → the rest passes (gap on record)",
       ev2["status"] == "passed" and ev2.get("waived") == ["walled"])

    # FLOOR: there is no delegated grant. Every scope reaches the owner.
    ok("the deputy has no delegated authority to read",
       not any(hasattr(SystemSpine, n) for n in
               ("get_deputy_delegated_authority", "set_deputy_delegated_authority")))


def _raises(fn) -> bool:
    try:
        fn(); return False
    except Exception:
        return True


def test_p4_permissions() -> None:
    print("P4 — boundary-scoped Bash + session approval memory")
    from superme_agent.core import permissions as P
    from pathlib import Path as _P
    roots = [_P("/tmp/wtx"), _P("/tmp/itemx")]
    ok("cd into the worktree scopes the command in",
       P._bash_scoped_into_boundary("cd /tmp/wtx && pytest -q", roots) is True)
    ok("git -C <worktree> scopes in",
       P._bash_scoped_into_boundary("git -C /tmp/wtx commit -m x", roots) is True)
    ok("a bare mutating command does NOT auto-scope (still asks)",
       P._bash_scoped_into_boundary("rm -rf .", roots) is False)
    ok("scoping OUTSIDE the boundary does not count",
       P._bash_scoped_into_boundary("cd /etc && rm x", roots) is False)
    ok("an outside absolute path still escapes even when scoped in",
       P._bash_escapes_boundary("cd /tmp/wtx && cat /etc/passwd", roots) is True)
    # Approval memory signature: coarse by program, subcommand-aware for multiplexers.
    ok("pytest keyed by program", P.approval_signature("Bash", {"command": "pytest -q t/"}) == "Bash:pytest")
    ok("git subcommands stay distinct",
       P.approval_signature("Bash", {"command": "git commit -m x"}) == "Bash:git commit"
       and P.approval_signature("Bash", {"command": "git push"}) == "Bash:git push")
    ok("non-Bash tools key by name", P.approval_signature("Edit", {"file_path": "/a"}) == "tool:Edit")
    import asyncio as _aio
    item = _P("/tmp/itemx")
    approve = P.scoped_writes_approve(item, P.deny_all)

    def _ask(tool: str, **inp):
        return _aio.run(approve(tool, inp))

    def _denied(tool: str, **inp) -> bool:
        return _ask(tool, **inp) is not True
    ok("Bash scoped into the item folder auto-allows",
       _ask("Bash", command="cd /tmp/itemx && python3 bench.py") is True)
    ok("an unscoped mutating Bash at the repo cwd still denies",
       _denied("Bash", command="python3 bench.py"))
    ok("scoped-in but naming an outside path denies",
       _denied("Bash", command="cd /tmp/itemx && python3 /etc/evil.py"))
    ok("scoping into someone ELSE's folder denies",
       _denied("Bash", command="cd /tmp/other && rm -rf ."))
    ok("writes inside the folder still auto-allow",
       _ask("Write", file_path="/tmp/itemx/artifacts/investigation.md") is True)
    ok("writes outside the folder still deny",
       _denied("Write", file_path="/tmp/repo/tally.py"))


def test_decision() -> None:
    print("decide_after_vet — the table and the breakers")
    # Sticky owner holds: anything not active yields without a trace.
    r = _d(item={"id": "i", "status": "awaiting_human", "phase": "vet"})
    ok("non-active item → none (owner hold is sticky)",
       r["action"] == "none" and r["record"] is False)
    r = _d(item={"id": "i", "status": "active", "phase": "vet", "done_at": "2026-07-17"})
    ok("terminal item → none", r["action"] == "none")
    r = _d(item={"id": "i", "status": "active", "phase": "build"})
    ok("item off vet (CAS pre-check) → none", r["action"] == "none")
    # --- the two faults split -----------------------------------------
    r = _d(turn_error=True)
    ok("a stopped vet run → error, held where it died",
       r["action"] == "error" and r["status"] == "error" and r["exit"] == "error")
    r = _d(turn_error=True, faults=2)
    ok("...and no second retry ladder — R1 already walked one",
       r["action"] == "error" and r["status"] == "error")
    r = _d(evidence={"status": "unverified"})
    ok("an empty ledger is a fault too (depth:none has its own honest representation)",
       r["action"] == "revet" and "recorded nothing" in r["fault"])
    r = _d(evidence={"status": "unverified"}, faults=2)
    ok("...and past the cap it also reaches review, not a park",
       r["action"] == "review" and r["exit"] == "system_fault")
    # Stale: always re-vet. The fingerprint ignores untracked files, so test litter cannot fake
    # it.
    r = _d(evidence={"status": "stale", "stale_checks": ["a"]})
    ok("stale → re-vet", r["action"] == "revet" and r["status"] == "active")
    r = _d(evidence={"status": "stale"}, attempts=[{"cycle": 1, "decision": "revet"}])
    ok("stale twice no longer stops the loop (budget is the backstop)", r["action"] == "revet")
    # Passed.
    r = _d(evidence={"status": "passed"})
    ok("passed → review at the owner's gate",
       r["action"] == "review" and r["status"] == "awaiting_human" and r["record"]
       and r["exit"] == "converged")
    # Failed + breakers — every breaker now EXITS TO REVIEW with a typed reason; none parks.
    failed = {"status": "failed", "failed_checks": ["beta"]}
    r = _d(evidence=failed, fingerprint="ff1", spent=10, budget=100)
    ok("failed, breakers clear → build cycle",
       r["action"] == "build" and r["status"] == "active" and r["failed"] == ["beta"])
    r = _d(evidence=failed, fingerprint="ff1", spent=100, budget=100)
    ok("budget breaker → review, exit `budget`",
       r["action"] == "review" and r["exit"] == "budget")
    ok("no decision anywhere returns the retired `halt` action",
       all(_d(**kw)["action"] != "halt" for kw in (
           dict(turn_error=True), dict(turn_error=True, faults=9),
           dict(evidence={"status": "unverified"}), dict(evidence={"status": "stale"}),
           dict(evidence=failed, fingerprint="f", spent=100, budget=100),
           dict(evidence=failed, fingerprint="f",
                attempts=[{"fingerprint": "f"}, {"fingerprint": "f"}]))))
    # Convergence counts APPEARANCES: one repeat is no proof of a wall, since build may be closing
    # in.
    r = _d(evidence=failed, fingerprint="ff1",
           attempts=[{"cycle": 1, "decision": "build", "fingerprint": "ff1"}])
    ok("a SECOND appearance still buys a cycle (trials, not one strike)", r["action"] == "build")
    r = _d(evidence=failed, fingerprint="ff1",
           attempts=[{"cycle": 1, "fingerprint": "ff1"}, {"cycle": 2, "fingerprint": "ff1"}])
    ok("the THIRD appearance exits → review, exit `not_converging`",
       r["action"] == "review" and r["exit"] == "not_converging" and "3 times" in r["reason"])
    r = _d(evidence=failed, fingerprint="ffA",
           attempts=[{"cycle": 1, "fingerprint": "ffA"}, {"cycle": 2, "fingerprint": "ffB"}])
    ok("appearances are counted at ANY distance — an intervening failure doesn't reset it",
       r["action"] == "build")
    r = _d(evidence=failed, fingerprint="ffA",
           attempts=[{"cycle": 1, "fingerprint": "ffA"}, {"cycle": 2, "fingerprint": "ffB"},
                     {"cycle": 3, "fingerprint": "ffA"}])
    ok("...and the oscillation fail A → B → A → A exits on the third A",
       r["action"] == "review" and r["exit"] == "not_converging")
    r = _d(evidence=failed, fingerprint="", attempts=[{"fingerprint": ""}, {"fingerprint": ""}])
    ok("empty fingerprint never trips the guard", r["action"] == "build")
    # The loop is human-free by contract, so a switch resting every hop inside it served no case.
    ok("no autorun switch survives — the loop never degrades to decide-and-page",
       "autorun" not in _inspect.signature(decide_after_vet).parameters
       and not hasattr(SystemSpine, "get_loop_autorun")
       and not hasattr(SystemSpine, "set_loop_autorun"))


def test_build_decision() -> None:
    print("decide_after_build — BV-A1 content walls never page mid-loop")
    from superme_agent.daemon.services.loop import decide_after_build
    live = {"id": "i", "status": "active", "phase": "build"}
    # A content wall is NOT a page: every outcome advances toward vet. Only an infra crash stops
    # here.
    for oc in ("success", "partial", "blocked", "clean_noop", "exhausted", "stagnated",
               "approval_required", None):
        r = decide_after_build(live, outcome=oc, turn_error=False)
        ok(f"outcome={oc!r} advances toward vet (no mid-build page)",
           r["stopping"] is False and r["klass"] == "advance")
    r = decide_after_build(live, outcome="success", turn_error=True)
    ok("turn_error (infra crash) → holds (BV-B will retry; today it pages)",
       r["stopping"] is True and r["klass"] == "infra")
    # `revise` stops the cycle and the DRIVER routes it. Routed from inside the report instead,
    # two writers moved one transition.
    r = decide_after_build(live, outcome="revise", turn_error=False)
    ok("outcome='revise' stops the build cycle instead of advancing to vet",
       r["stopping"] is True and r["klass"] == "revise")
    # An owner-moved/paused/terminal item is theirs — the loop yields, never pages.
    for it in ({"status": "awaiting_human", "phase": "build"},
               {"status": "active", "phase": "review"},
               {"status": "active", "phase": "build", "done_at": "2026-07-21"}):
        r = decide_after_build({"id": "i", **it}, outcome="success", turn_error=False)
        ok(f"moved/paused ({it}) → yields (klass=moved)",
           r["stopping"] is True and r["klass"] == "moved")
    # turn_error on an already-moved item still reads as `moved` (theirs wins over infra).
    r = decide_after_build({"id": "i", "status": "active", "phase": "review"},
                           outcome="success", turn_error=True)
    ok("moved beats infra — a moved item yields, not pages", r["klass"] == "moved")


def _write_item(dev_root: Path, iid: str, phase: str = "vet", status: str = "active",
                worktree: str | None = None) -> Path:
    d = dev_root / "work-items" / iid
    (d / "artifacts").mkdir(parents=True, exist_ok=True)
    wt = f"git_worktree: {worktree}\n" if worktree else ""
    (d / "item.md").write_text(
        f"---\nid: {iid}\ntitle: t\nkind: implementation\nphase: {phase}\nstatus: {status}\n"
        f"{wt}created_at: {date.today().isoformat()}\nupdated_at: {date.today().isoformat()}\n---\n", encoding="utf-8")
    return d


def test_cas(tmp: Path) -> None:
    print("CAS phase flip")
    dev_root = tmp / "devroot-cas"
    _write_item(dev_root, "c1", phase="vet")
    ok("flip wins when the phase matches", L._cas_phase(dev_root, "c1", "vet", "review") is True)
    it = L._dev.read_work_item(dev_root, "c1")
    ok("phase landed", str(it.get("phase")) == "review")
    ok("a second identical flip loses (phase moved)",
       L._cas_phase(dev_root, "c1", "vet", "build") is False)
    ok("missing item loses", L._cas_phase(dev_root, "nope", "vet", "build") is False)


def test_spine_loop_settings(tmp: Path) -> None:
    print("spine: loop budget + the phase-token meter")
    sp = SystemSpine(db_path=tmp / "s5.db")
    ok("budget defaults to the calibrated constant",
       sp.get_loop_budget() == sp.DEFAULT_LOOP_BUDGET == 500_000)
    sp.set_loop_budget(123_000)
    ok("system budget is settable", sp.get_loop_budget() == 123_000)
    ok("item frontmatter overrides the system default",
       sp.effective_loop_budget("r", "9000") == 9000
       and sp.effective_loop_budget("r", 7000) == 7000)
    ok("garbage item value falls back to system",
       sp.effective_loop_budget("r", "lots") == 123_000
       and sp.effective_loop_budget("r", None) == 123_000)
    sp.set_loop_budget(None)
    ok("clearing returns the default", sp.get_loop_budget() == 500_000)
    # The meter: build+vet rows count (live AND finished), other phases don't.
    rid = sp.start_item_run("r", mode="dev", feature="vet", item_id="i1", phase="vet")
    sp.set_item_run_tokens("r", "i1", tokens=111, ctx_pct=1)
    sp.finish_item_run("r", "i1")
    sp.start_item_run("r", mode="dev", feature="build", item_id="i1", phase="build")
    sp.set_item_run_tokens("r", "i1", tokens=200, ctx_pct=1)   # LIVE row — must still count
    ok("meter sums build+vet (live rows included)",
       rid is not None and sp.item_phase_tokens("r", "i1") == 311)
    sp.finish_item_run("r", "i1")
    sp.start_item_run("r", mode="dev", feature="plan", item_id="i1", phase="plan")
    sp.set_item_run_tokens("r", "i1", tokens=5000, ctx_pct=1)
    sp.finish_item_run("r", "i1")
    ok("other phases don't count toward the loop budget",
       sp.item_phase_tokens("r", "i1") == 311)
    ok("phase selection is a parameter",
       sp.item_phase_tokens("r", "i1", phases=("plan",)) == 5000)


from dataclasses import dataclass


@dataclass
class _Ctx:
    """A dataclass ctx stand-in — `_loop_ctx` swaps cwd via dataclasses.replace, so a
    SimpleNamespace won't do."""
    internal_root: Path
    cwd: Path
    id: str = "r"
    mode: str = "dev"


def test_start_guards(tmp: Path) -> None:
    print("start_vet_run / start_build_cycle guard chains")
    wt = tmp / "wt-start"
    wt.mkdir()
    # start_* read via the module's _dev singleton against ctx.internal_root/"dev".
    ctx = _Ctx(internal_root=tmp / "devroot-start", cwd=tmp)
    dev_root = ctx.internal_root / "dev"

    async def run() -> None:
        started, why = L.start_vet_run(ctx, "r", "missing")
        ok("vet: missing item refused", started is False and "not found" in why)
        _write_item(dev_root, "s1", phase="build", worktree=str(wt))
        started, why = L.start_vet_run(ctx, "r", "s1")
        ok("vet: wrong phase refused", started is False and "not in vet" in why)
        _write_item(dev_root, "s2", phase="vet")
        started, why = L.start_vet_run(ctx, "r", "s2")
        ok("vet: no worktree refused", started is False and "worktree" in why)
        _write_item(dev_root, "s3", phase="vet", status="awaiting_child", worktree=str(wt))
        started, why = L.start_vet_run(ctx, "r", "s3")
        ok("vet: paused item refused", started is False and "not runnable" in why)
        # Happy path with the expensive bits faked: run-lock taken, runner stubbed.
        calls: list[tuple] = []
        real_begin, real_runner, real_reset = L.begin_run, L._run_background_vet, L.reset_vet_thread
        fired = asyncio.Event()

        async def fake_runner(*a, **k):
            fired.set()
        L.begin_run = lambda *a, **k: 42
        L._run_background_vet = fake_runner
        L.reset_vet_thread = lambda c, it: calls.append("reset") or False
        try:
            _write_item(dev_root, "s4", phase="vet", worktree=str(wt))
            started, why = L.start_vet_run(ctx, "r", "s4")
            await asyncio.wait_for(fired.wait(), 5)
            ok("vet: happy path starts (fresh-mint reset fired first)",
               started is True and calls == ["reset"])
            L.begin_run = lambda *a, **k: None
            started, why = L.start_vet_run(ctx, "r", "s4")
            ok("vet: run-lock contention refused", started is False and "in progress" in why)
        finally:
            L.begin_run, L._run_background_vet, L.reset_vet_thread = real_begin, real_runner, real_reset
        # Build-cycle guards (the failure hop — needs a vet report).
        started, why = L.start_build_cycle(ctx, "r", "s2")
        ok("build: wrong phase refused", started is False and "not in build" in why)
        _write_item(dev_root, "s5", phase="build", worktree=str(wt))
        started, why = L.start_build_cycle(ctx, "r", "s5")
        ok("build: no vet report to hand over → refused",
           started is False and "report" in why)
        # Build-FIRST (the loop's opening cycle) — SAME guards EXCEPT it needs no vet report.
        started, why = L.start_first_build(ctx, "r", "s2")
        ok("first-build: wrong phase (vet) refused", started is False and "not in build" in why)
        _write_item(dev_root, "s6", phase="build")   # no worktree
        started, why = L.start_first_build(ctx, "r", "s6")
        ok("first-build: no worktree refused", started is False and "worktree" in why)
        _write_item(dev_root, "s7", phase="build", status="awaiting_child", worktree=str(wt))
        started, why = L.start_first_build(ctx, "r", "s7")
        ok("first-build: paused item refused", started is False and "not runnable" in why)
        # Happy path: build-first STARTS at phase build with NO vet report present (the whole point).
        calls2: list[str] = []
        real_begin, real_build = L.begin_run, L._run_background_build
        fired2 = asyncio.Event()

        async def fake_build(*a, **k):
            calls2.append(str(k.get("trigger") or (a[-1] if a else "")))
            fired2.set()
        L.begin_run = lambda *a, **k: 77
        L._run_background_build = fake_build
        try:
            _write_item(dev_root, "s8", phase="build", worktree=str(wt))   # NO vet report on disk
            started, why = L.start_first_build(ctx, "r", "s8")
            await asyncio.wait_for(fired2.wait(), 5)
            ok("first-build: starts with no vet report present", started is True and why == "build")
            ok("first-build: hands the build a plan-pointed trigger (not a vet report)",
               len(calls2) == 1 and "plan.md" in calls2[0] and "vet-report" not in calls2[0])
        finally:
            L.begin_run, L._run_background_build = real_begin, real_build
        # The loop never parks, so no build rests at a paged reason and this entry point had no
        # caller.
    asyncio.run(run())


def test_registration() -> None:
    print("route + taxonomy + skill contracts")
    from superme_agent.daemon.server import app
    # How FastAPI stores an included router is private. The generated document is the contract.
    paths = set(app.openapi()["paths"])
    # One door onto the dispatcher both already shared, so no phase can be missing a firer.
    ok("POST /dev/work-items/{item_id}/run is registered", "/dev/work-items/{item_id}/run" in paths)
    ok("...and the per-phase doors it replaced are gone",
       "/dev/work-items/{item_id}/vet" not in paths and "/dev/work-items/{item_id}/plan" not in paths)
    ok("POST /dev/work-items/{item_id}/continue is GONE (retired with the loop's parking state)",
       "/dev/work-items/{item_id}/continue" not in paths)
    from superme_agent.harness.tools.run_tools import RUN_OUTCOMES
    ok("`partial` is a valid run outcome (BV-A1)", "partial" in RUN_OUTCOMES)
    ok("vet runs classify as work-item spend", category_for("vet") == "workitem")
    base = Path("superme_agent/harness/plugins/superme-dev/skills")
    # Whitespace-normalized: prose wraps at ~100 cols, phrase assertions must not care.
    vet = " ".join((base / "vet" / "SKILL.md").read_text(encoding="utf-8").split())
    build = " ".join((base / "build" / "SKILL.md").read_text(encoding="utf-8").split())
    ok("vet skill: execution-first, record-per-check, never fix",
       "record_verification" in vet and "FAIL" in vet
       and "Fixes belong to the build session" in vet)
    ok("build skill carries the cycle-report contract",
       "build-vet-<n>.md" in build and "never advance the phase" in build)
    ok("build skill carries the record-and-carry rule (a wall → record, not a stall)",
       "## Assumptions" in build and "request_authorization" in build
       and "never `blocked`" in build)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        repo = make_repo(tmp)
        test_attempts_ledger(tmp)
        test_fingerprint(tmp, repo)
        test_fingerprint_scope(tmp)
        test_no_progress_guard(tmp, repo)
        test_evidence_check_guard(tmp, repo)
        test_depth_none(tmp, repo)
        test_evidence_orphan_scope(tmp, repo)
        test_deferred_authorization(tmp, repo)
        test_p4_permissions()
        test_decision()
        test_build_decision()
        test_cas(tmp)
        test_spine_loop_settings(tmp)
        test_start_guards(tmp)
        test_registration()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
