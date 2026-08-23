"""Resume: re-firing the background run of an item whose RUN stopped.

Nothing is rewound, which is what makes it cheap enough to be a button. Not Continue: that
finalizes a build that hit a wall, where the run succeeded and the work stopped.

Run: PYTHONPATH=. python -m scripts.test_resume
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.daemon.services.drilldown import ACTION_HOMES, _actions
from superme_agent.daemon.services.resume import RESUMABLE_PHASES, resume_reason
from scripts.sources import src

PASS = 0


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


# ── the activation rule ─────────────────────────────────────────────────────────────────────────

def test_resume_reason() -> None:
    print("\n[rule] one function, shared by the button and the route")

    stopped = {"id": "a", "status": "error", "phase": "build",
               "error_reason": "upstream was unavailable"}
    can, why = resume_reason(stopped, running=False)
    ok("a stopped item at a resumable phase can resume", can)
    ok("…and the reason says what clicking does", "re-fires the build run" in why)
    ok("…and promises nothing is rewound",
       "branch" in why and "worktree" in why and "stand" in why)

    can, why = resume_reason({"status": "awaiting_human", "phase": "review"}, running=False)
    ok("a parked item cannot resume — nothing stopped", not can)
    ok("…and says when the button WOULD appear", "when a run dies" in why)

    can, why = resume_reason(stopped, running=True)
    ok("an item with a run already in flight cannot resume", not can)
    ok("…and says so plainly", "already in flight" in why)

    can, why = resume_reason({"status": "error", "done_at": "2026-01-01"}, running=False)
    ok("a terminal item cannot resume", not can and "terminal" in why)

    can, why = resume_reason({"status": "error", "phase": "queued"}, running=False)
    ok("a phase with no background run cannot resume", not can)
    ok("…naming the phase rather than failing silently", "queued" in why)

    # Every phase owning a background run must be resumable, or an outage produces a dead-end
    # item.
    for phase in ("triage", "plan", "build", "vet", "investigate", "review", "close"):
        ok(f"`{phase}` is resumable",
           resume_reason({"status": "error", "phase": phase}, running=False)[0])
    ok("the resumable set is exactly those seven", len(RESUMABLE_PHASES) == 7)


# ── the drilldown control ───────────────────────────────────────────────────────────────────────

def _acts(item: dict, **kw) -> dict:
    state = {"phase": item.get("phase"), "terminal": bool(item.get("done_at")),
             "at_gate": kw.pop("at_gate", False), "blocked_by": []}
    out = _actions(item, state, running=kw.pop("running", False), git_health=None,
                   paged=kw.pop("paged", None), next_phase=None, review_mode="fast")
    return {a["id"]: a for a in out}


def test_drilldown_control() -> None:
    print("\n[drilldown] Resume is the ONE control for a stopped run (Continue retired 2026-07-31)")

    ok("Resume has a home in the action bar", ACTION_HOMES.get("resume") == "actions")

    stopped = {"id": "a", "status": "error", "phase": "build", "error_reason": "outage"}
    acts = _acts(stopped)
    ok("a stopped build offers Resume", acts["resume"]["active"])
    # Continue is GONE, not greyed: its trigger cannot occur now that the loop never parks.
    ok("Continue is not rendered at all", "continue" not in acts)
    ok("…and it has no home either", "continue" not in ACTION_HOMES)

    # A build that is merely parked (not stopped) is nobody's Resume.
    parked = {"id": "b", "status": "awaiting_human", "phase": "build"}
    acts = _acts(parked, paged={"why": "commit refused"})
    ok("a parked build does not offer Resume", not acts["resume"]["active"])
    ok("…explaining that nothing stopped", "nothing has stopped" in acts["resume"]["reason"])

    ok("a running item offers neither", not _acts(stopped, running=True)["resume"]["active"])

    # A greyed control that explains itself teaches. An absent one hides the rule.
    for case in (stopped, parked, {"id": "c", "status": "active", "phase": "triage"}):
        ok(f"Resume is always RENDERED (phase={case['phase']}, status={case['status']})",
           "resume" in _acts(case))


# ── the failure path ────────────────────────────────────────────────────────────────────────────

def test_failed_resume_restores(dev_root: Path) -> None:
    print("\n[failure] a resume that starts nothing must not leave the item `active` with no run")

    dev = DevKnowledgeService()
    d = dev_root / "work-items" / "r1"
    d.mkdir(parents=True)
    (d / "item.md").write_text(
        "---\nid: r1\ntitle: Fixture\nphase: build\nstatus: active\nupdated_at: 2026-01-01\n---\n\nb\n", encoding="utf-8")
    dev.set_work_item_error(dev_root, "r1", "the build run stopped — upstream was unavailable")

    body = src("superme_agent/daemon/services/resume.py")
    ok("the status is cleared FIRST (every firer refuses a non-active item)",
       body.index('set_work_item_status(dev_root, item_id, "active")') < body.index("_fire("))
    ok("…and restored on a failed fire", "if not started:" in body
       and "_dev.set_work_item_error(dev_root, item_id, was" in body)
    ok("…with the ORIGINAL reason, not a new one invented by the failure",
       "was = str(item.get(\"error_reason\")" in body)
    ok("a successful resume leaves a run.resume event", '"run.resume"' in body)
    ok("…attributed to the owner", 'actor="owner"' in body)

    dev.set_work_item_status(dev_root, "r1", "active")
    ok("clearing the status clears the reason",
       not (dev.read_work_item(dev_root, "r1") or {}).get("error_reason"))


# ── the wiring ──────────────────────────────────────────────────────────────────────────────────

def test_wiring() -> None:
    print("\n[wiring] one dispatch table, shared by the button now and auto-resume later")

    body = src("superme_agent/daemon/services/resume.py")
    ok("the dispatcher reuses the workflow's OWN firers, not a private copy",
       all(f in body for f in ("start_build_cycle", "start_vet_run", "fire_auto_triage",
                               "fire_review_entry", "fire_close_run")))
    ok("…and the restart reconciler calls that same function, never its own copy",
       "from .services.resume import resume_item" in src("superme_agent/daemon/lifespan.py"))

    route = src("superme_agent/daemon/routers/dev/work_items.py")
    ok("the route exists", '"/dev/work-items/{item_id}/resume"' in route)
    ok("…and calls the shared service rather than dispatching itself",
       "from ...services.resume import resume_item" in route)
    ok("…409ing on the same conditions the button reads", "status_code=409" in route)
    ok("…and Resume and Re-run stay two routes over two services",
       '"/dev/work-items/{item_id}/rerun"' in route
       and "from ...services.rerun import rerun_item" in route)

    drill = src("superme_agent/daemon/services/drilldown.py")
    ok("the drilldown imports the rule instead of restating it",
       "from .resume import resume_reason" in drill)

    api = src("web/frontend/src/lib/api/dev.ts")
    ok("the FE has a resume call", "export function resumeWorkItem(" in api)
    ok("…pointed at the route", "/resume`" in api)
    ok("…and the WorkItem type carries the reason", "error_reason?: string | null" in api)

    modal = src("web/frontend/src/features/dev/WorkItemModal.tsx")
    ok("the drilldown dispatcher runs it", "resume: () => resumeWorkItem(" in modal)

    panels = src("web/frontend/src/features/dev/panels.tsx")
    ok("the CARD offers Resume too (the owner asked for both surfaces)",
       "onResume(it)" in panels)
    ok("…only when stopped, so the card stays a glance everywhere else",
       "{stopped && onResume && (" in panels)
    ok("…without also opening the drilldown", "e.stopPropagation()" in panels)
    ok("…and its tooltip carries the stored reason", "it.error_reason ?" in panels)

    dash = src("web/frontend/src/features/dev/DevDashboard.tsx")
    ok("the board wires the handler", "onResume={resumeItem}" in dash)
    ok("…surfacing failures in the same slot as every other write here",
       "Couldn't resume —" in dash)


def main() -> None:
    test_resume_reason()
    test_drilldown_control()
    with TemporaryDirectory() as td:
        test_failed_resume_restores(Path(td))
    test_wiring()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
