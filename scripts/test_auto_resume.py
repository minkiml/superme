"""Auto-resume on a healthy restart.

An orphaned item is labelled `error`, then resumed through the same path the owner's button
uses — but only when its dead run was a phase's own background work.

Run: PYTHONPATH=. python -m scripts.test_auto_resume
"""


from superme_agent.daemon.lifespan import _AUTO_RESUME_FEATURES, _MAX_AUTO_RESUME
from superme_agent.daemon.services.resume import RESUMABLE_PHASES
from scripts.sources import src

PASS = 0


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


# ── what auto-resumes, and what deliberately doesn't ────────────────────────────────────────────

def test_auto_resume_set() -> None:
    print("\n[scope] only a phase's OWN background run is re-fired automatically")

    ok("the auto-resume set is exactly the phases resume can dispatch",
       _AUTO_RESUME_FEATURES == set(RESUMABLE_PHASES))

    # The exclusions are the point. Re-running the PHASE would not be re-running what died.
    for feature in ("chat", "deputy", "resolve", "compact", "distill", "write", "sweep"):
        ok(f"`{feature}` never auto-resumes — re-running the phase isn't re-running what died",
           feature not in _AUTO_RESUME_FEATURES)

    life = src("superme_agent/daemon/lifespan.py")
    ok("…and those still get the label + a Resume button, not silence",
       "left for" in life and "your Resume" in life)


def test_label_then_resume() -> None:
    print("\n[order] label FIRST, resume second — so a failed resume lands on the truth")

    life = src("superme_agent/daemon/lifespan.py")
    body = life.split("def _reconcile_orphaned_items")[1].split("def _reconcile_stranded")[0]
    ok("the item is marked `error` before any resume is attempted",
       body.index("set_work_item_error") < body.index("resume_item("))
    ok("…with a reason naming the restart", "a daemon restart stopped the" in body)
    ok("…and the phase it stopped in", "{phase} run" in body)
    ok("the resume goes through the SHARED service, not a private dispatch",
       "from .services.resume import resume_item" in life)
    ok("a resume that doesn't start leaves the item at error, logged",
       "held at error" in body)

    # The old behaviour is gone, not merely bypassed: parking at awaiting_human was the bug.
    ok("orphans are no longer parked at `awaiting_human`",
       'set_work_item_status(dev_root, item_id, "awaiting_human")' not in body)
    ok("…each is labelled `error` BEFORE any resume is attempted",
       body.index("set_work_item_error") < body.index("resume_item("))
    ok("terminal items are still left to the close reconciler",
       'str(it.get("status")) == "done"' in body)


def test_cap_is_stated_not_silent() -> None:
    print("\n[cap] a restart after a long outage must not fire a whole cohort unasked")

    ok("there is a cap", isinstance(_MAX_AUTO_RESUME, int) and _MAX_AUTO_RESUME > 0)
    life = src("superme_agent/daemon/lifespan.py")
    ok("…and what it drops is LOGGED, never silently skipped",
       "over the auto-resume cap" in life and "NOT " in life)
    ok("…with the items named", 'join(deferred)' in life)
    ok("…and they keep their Resume button", "Resume button" in life)
    ok("…and the cap bounds the resumes themselves, not just the message",
       "if resumed >= _MAX_AUTO_RESUME:" in life)


def test_wired_into_startup() -> None:
    print("\n[startup] both reconcilers actually run, in the right order")

    life = src("superme_agent/daemon/lifespan.py")
    boot = life.split("async def lifespan")[1]
    ok("orphan reconcile runs at boot", "_reconcile_orphaned_items(_orphans)" in boot)
    ok("…after the close reconcile, so a mid-close item is finished first, not resumed",
       boot.index("_reconcile_close_steps()") < boot.index("_reconcile_orphaned_items"))
    ok("the stranded-proposal reconcile runs too", "_reconcile_stranded_proposals()" in boot)
    ok("every reconciler is best-effort — housekeeping must never stop the daemon booting",
       life.count("(non-fatal)") >= 4)


# ── the learning pipeline's dead end ────────────────────────────────────────────────────────────

def test_stranded_proposal() -> None:
    print("\n[proposals] a dead `write` run no longer strands its proposal forever")

    life = src("superme_agent/daemon/lifespan.py")
    body = life.split("def _reconcile_stranded_proposals")[1]
    ok("it looks for proposals stuck at `writing`", 'status="writing"' in body)
    ok("…and returns them to `proposed` — where they were before the approval",
       'set_proposal_status(pid, "proposed")' in body)
    ok("…which is the SAME reset the write runner does on its own failure path",
       'set_proposal_status(proposal_id, "proposed")'
       in src("superme_agent/daemon/services/learning.py"))
    ok("…leaving a trail the owner can read", '"write.orphaned"' in body)
    ok("no run is re-fired — a write is cheap to re-approve and the owner gates it anyway",
       "resume_item" not in body)
    ok("…and the pass is wired into startup beside the other reconcilers",
       "_reconcile_stranded_proposals()" in life)


# ── the merge hole the plan flagged ─────────────────────────────────────────────────────────────

def test_merge_is_already_idempotent() -> None:
    """The plan listed "merge checks `git_merge_commit` first" as work. It is already true — the
    guard already shipped. Pinned here rather than rebuilt, so the claim stays checked."""
    print("\n[merge] re-firing a merge after a crash cannot merge twice")

    git = src("superme_agent/core/git_layer.py")
    ok("the never-merge-twice guard reads the RECORDED merge commit first",
       "def _is_merged(" in git and 'if commit_exists(repo_dir, merge_commit or "")' in git)
    ok("…with ancestry only as a fallback, reached after the recorded sha misses",
       '"merge-base", "--is-ancestor", branch, target' in git
       and git.index('if commit_exists(repo_dir, merge_commit or "")')
       < git.index('"merge-base", "--is-ancestor", branch, target'))
    ok("the merge act passes the item's stamp in",
       'merged_commit=item.get("git_merge_commit")'
       in src("superme_agent/daemon/services/git_ops.py"))
    ok("…and an already-merged branch returns instead of re-merging",
       '{"already_merged": True, "merged": False}' in git)


def main() -> None:
    test_auto_resume_set()
    test_label_then_resume()
    test_cap_is_stated_not_silent()
    test_wired_into_startup()
    test_stranded_proposal()
    test_merge_is_already_idempotent()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
