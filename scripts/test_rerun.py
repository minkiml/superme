"""Re-run: starting a work-item over in place.

Identity and relations survive, the produced work goes, the branch and trace stand. The
frontmatter rule is a KEEPLIST, so a field added later cannot ride through a re-run unnoticed.

Run: PYTHONPATH=. python -m scripts.test_rerun
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.daemon.services.drilldown import ACTION_HOMES, _actions
from superme_agent.daemon.services.rerun import rerun_reason

PASS = 0
ROOT = Path(__file__).resolve().parents[1]


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def src(rel: str) -> str:
    return (ROOT / rel).read_text()


# An item at the END of a full lifecycle, so the keeplist meets real accumulation.
LIVED = """---
id: aa11bb22cc33
root_id: aa11bb22cc33
parent_id: null
title: "budgets with an over-budget warning"
kind: implementation
phase: review
autopilot: true
status: awaiting_human
model: sonnet
effort: high
done_at: null
outcome: null
artifacts: [{"type": "plan", "path": "artifacts/plan.md"}]
inbox_id: 237
wave: w-2
deliverable: d-tally
cohort: "c-0731"
after: []
session_id: null
triaged_at: 2026-07-30
session_intake: "s-intake"
session_build: "s-build"
session_vet: "s-vet"
handoffs_promoted: 1
seen_at: 2026-07-31
created_at: 2026-07-29
updated_at: 2026-07-31
git_branch: "item/aa11bb22cc33-budgets"
git_worktree: "/tmp/wt/aa11bb22cc33"
git_base: "main"
git_pr_opened_at: "2026-07-31T04:04:04"
future_lifecycle_field: "written by a slice that does not exist yet"
---
I want tally to warn me when I am overspending.
"""


def _seed(dev_root: Path, item_id: str = "aa11bb22cc33") -> Path:
    d = dev_root / "work-items" / item_id
    (d / "artifacts").mkdir(parents=True)
    (d / "reports").mkdir()
    (d / "checkpoints").mkdir()
    (d / "preliminary").mkdir()
    (d / "item.md").write_text(LIVED.replace("aa11bb22cc33", item_id))
    (d / "artifacts" / "plan.md").write_text("# Plan\n")
    (d / "artifacts" / "build-vet-1.md").write_text("# Cycle 1\n")
    (d / "reports" / "report-build.md").write_text("# Build\n")
    (d / "checkpoints" / "20260731-000000.md").write_text("checkpoint\n")
    (d / "deputy-log.jsonl").write_text('{"verdict": "approve"}\n')
    (d / "preliminary" / "handoff-brief.md").write_text("the original hand-off\n")
    return d


# ── what survives the reset, and what does not ──────────────────────────────────────────────────

def test_keeps_and_clears(dev_root: Path) -> None:
    print("\n[reset] identity and the ask survive; the work does not")

    dev = DevKnowledgeService()
    d = _seed(dev_root)
    res = dev.reset_work_item(dev_root, "aa11bb22cc33")
    it = dev.read_work_item(dev_root, "aa11bb22cc33") or {}

    ok("the id is unchanged — a re-run is the SAME item", it["id"] == "aa11bb22cc33")
    ok("…and so is the folder it lives in", d.is_dir())
    ok("the ask (the item.md body) is untouched",
       "warn me when I am overspending" in str(it.get("description")))

    for field, val in (("title", "budgets with an over-budget warning"), ("kind", "implementation"),
                       ("wave", "w-2"), ("deliverable", "d-tally"), ("cohort", "c-0731"),
                       ("inbox_id", 237), ("model", "sonnet"), ("effort", "high"),
                       ("autopilot", True), ("created_at", "2026-07-29"),
                       ("git_branch", "item/aa11bb22cc33-budgets"), ("git_base", "main")):
        # `str()` on both sides: the point is that the VALUE round-trips, not which type it lands
        # as.
        ok(f"`{field}` survives — {'identity/relation' if field in ('title', 'kind', 'wave', 'deliverable', 'cohort', 'inbox_id') else 'owner config or the code line'}",
           str(it.get(field)) == str(val))

    for field in ("triaged_at", "handoffs_promoted", "seen_at", "session_intake", "session_build",
                  "session_vet", "git_worktree", "git_pr_opened_at", "outcome"):
        ok(f"`{field}` is GONE — it describes the attempt that was just discarded",
           field not in it)
    ok("`artifacts` is emptied, not left pointing at deleted files", it.get("artifacts") == [])
    ok("`session_id` is null — a fresh start does not inherit the old thread",
       it.get("session_id") in (None, ""))

    # The keeplist's whole purpose: the safe direction on a field nobody has classified.
    ok("an UNKNOWN field is dropped, not silently carried "
       "(the keeplist fails safe; a clear-list would fail the other way)",
       "future_lifecycle_field" not in it)


def test_entry_and_no_counter(dev_root: Path) -> None:
    print("\n[entry] back to the kind profile's first phase — and NO generation counter")

    dev = DevKnowledgeService()
    _seed(dev_root, "bb22cc33dd44")
    first = dev.reset_work_item(dev_root, "bb22cc33dd44")
    ok("re-enters at the kind profile's FIRST phase, not a remembered one",
       first["phase"] == "triage")
    it = dev.read_work_item(dev_root, "bb22cc33dd44") or {}
    ok("…which is what the item now reads as", it.get("phase") == "triage")
    ok("…active, so the entry run has something to run against", it.get("status") == "active")

    # The counter is GONE: the soft delete ended the mismatch it explained, and a number nobody
    # needs goes stale.
    dev.reset_work_item(dev_root, "bb22cc33dd44")
    disk = (dev_root / "work-items" / "bb22cc33dd44" / "item.md").read_text()
    ok("a re-run writes NO generation field", "generation:" not in disk)
    ok("…and the read path exposes none", "generation" not in (
        dev.read_work_item(dev_root, "bb22cc33dd44") or {}))
    ok("…nor does the reset report one", "generation" not in first)


def test_soft_delete_split(dev_root: Path) -> None:
    """The reader split. A re-run must leave the ITEM
    looking fresh while the PROJECT's accounting still counts what the attempt cost. Nothing is
    deleted; rows are stamped `discarded_at` and readers choose a side."""
    print("\n[soft delete] the item forgets; the ledger does not")

    import tempfile
    from superme_agent.core.spine import SystemSpine
    with tempfile.TemporaryDirectory() as td:
        sp = SystemSpine(Path(td) / "s.db", Path(td) / "repos.yaml")
        rid, iid = "r", "item1"
        run = sp.start_item_run(rid, mode="dev", feature="build", item_id=iid, phase="build")
        sp.finish_run(run, status="done", tokens=1000,
                      usage={"input_tokens": 600, "output_tokens": 400})
        ok("before the re-run the item's budget sees the spend",
           sp.item_phase_tokens(rid, iid) == 1000)
        ok("…and its Runs pane has the row", len(sp.runs_for_item(rid, iid)) == 1)

        n = sp.discard_item_trace(rid, iid, at="2026-07-31T00:00:00+00:00")
        ok("the re-run stamps the row, it does not delete it", n["runs"] == 1)

        # This feeds the loop's budget breaker: counting a discarded attempt kills the fresh one.
        ok("the LOOP BUDGET no longer counts the discarded attempt",
           sp.item_phase_tokens(rid, iid) == 0)
        ok("the item's Runs pane is empty — it reads as a fresh item",
           sp.runs_for_item(rid, iid) == [])
        ok("the card/header total drops it too",
           sp.run_stats(rid).get(iid) is None)

        # …and the other side of the split: the project still knows what it paid.
        hist = sp.run_history(rid)
        ok("REPO history still carries the row — the attempt really was paid for",
           any(int(r["id"]) == int(run) for r in hist))
        ok("…with the stamp on it, so a reader can tell", any(
            int(r["id"]) == int(run) and r.get("discarded_at") for r in hist))

        ok("stamping twice is idempotent — the first stamp is not overwritten",
           sp.discard_item_trace(rid, iid, at="2026-08-01T00:00:00+00:00")["runs"] == 0)


def test_files(dev_root: Path) -> None:
    print("\n[files] produced work goes; pushed INPUT stays")

    dev = DevKnowledgeService()
    d = _seed(dev_root, "cc33dd44ee55")
    res = dev.reset_work_item(dev_root, "cc33dd44ee55")

    for name in ("artifacts/plan.md", "artifacts/build-vet-1.md", "reports/report-build.md",
                 "checkpoints/20260731-000000.md", "deputy-log.jsonl"):
        ok(f"`{name}` is deleted — it is last attempt's work", not (d / name).exists())
    ok("`preliminary/` STAYS — it is the pushed input, not work this item produced",
       (d / "preliminary" / "handoff-brief.md").exists())
    ok("`artifacts/` is left EMPTY but present, exactly as a newly-created item has it",
       (d / "artifacts").is_dir() and not list((d / "artifacts").iterdir()))
    ok("…and what was removed is reported back, not done silently",
       set(res["removed"]) == {"artifacts/", "reports/", "checkpoints/", "deputy-log.jsonl"})


def test_upstreams_still_hold(dev_root: Path) -> None:
    print("\n[upstreams] a reset item must no more start against unlanded work than a new one")

    dev = DevKnowledgeService()
    up = dev.create_work_item(dev_root, "the upstream", kind="implementation")
    child = dev.create_work_item(dev_root, "the dependent", after=[up["id"]], kind="implementation")
    res = dev.reset_work_item(dev_root, child["id"])
    ok("with an open upstream the reset parks at `awaiting_upstream`",
       res["status"] == "awaiting_upstream")
    ok("…the same rule `create_work_item` applies at birth (one rule, not two)",
       'status = "awaiting_upstream"' in src("superme_agent/core/dev_knowledge.py"))
    ok("…and the peer edge itself survives the reset",
       (dev.read_work_item(dev_root, child["id"]) or {}).get("after") == [up["id"]])

    dev.set_work_item_terminal(dev_root, up["id"])
    ok("once the upstream lands, a reset comes back `active`",
       dev.reset_work_item(dev_root, child["id"])["status"] == "active")


# ── the activation rule ─────────────────────────────────────────────────────────────────────────

def test_rerun_reason() -> None:
    print("\n[rule] one function, shared by the button and the route")

    can, why = rerun_reason({"status": "error", "phase": "build"}, running=False)
    ok("a stopped item can be re-run", can)
    ok("…and the reason NAMES what is cleared",
       "cleared" in why and "sessions" in why and "artifacts" in why)
    ok("…including the two halves added on 2026-07-31: the trace leaves the view, and the branch "
       "is re-cut so the next attempt does not build on the failed one",
       "run trace" in why and "re-cut" in why)
    ok("…and says plainly that nothing is destroyed",
       "Nothing is destroyed" in why and "totals" in why)

    ok("an item that is merely parked can ALSO be re-run — the point is that it is always there",
       rerun_reason({"status": "awaiting_human", "phase": "review"}, running=False)[0])
    ok("…and one mid-phase", rerun_reason({"status": "active", "phase": "build"}, running=False)[0])

    can, why = rerun_reason({"status": "active", "phase": "build"}, running=True)
    ok("an item with a run in flight cannot", not can and "in flight" in why)

    can, why = rerun_reason({"status": "done", "done_at": "2026-07-31"}, running=False)
    ok("a finished item cannot — its branch is landed", not can)
    ok("…and it says what to do instead, rather than just refusing", "a new item" in why)


def test_drilldown_control() -> None:
    print("\n[drilldown] Re-run sits beside Resume, and is never a card button")

    ok("Re-run has a home in the action bar", ACTION_HOMES.get("rerun") == "actions")

    def _acts(item: dict, **kw) -> dict:
        state = {"phase": item.get("phase"), "terminal": bool(item.get("done_at")),
                 "at_gate": False, "blocked_by": []}
        out = _actions(item, state, running=kw.pop("running", False), git_health=None,
                       paged=None, next_phase=None, review_mode="fast")
        return {a["id"]: a for a in out}

    acts = _acts({"id": "a", "status": "error", "phase": "build"})
    ok("a stopped item offers BOTH — Resume first, Re-run as the fallback",
       acts["resume"]["active"] and acts["rerun"]["active"])
    acts = _acts({"id": "b", "status": "awaiting_human", "phase": "review"})
    ok("a parked item offers Re-run while Resume stays inactive (nothing stopped)",
       acts["rerun"]["active"] and not acts["resume"]["active"])
    ok("a terminal item offers neither",
       not _acts({"id": "c", "status": "done", "done_at": "x", "phase": "close"})["rerun"]["active"])
    ok("a running item offers neither",
       not _acts({"id": "d", "status": "active", "phase": "build"}, running=True)["rerun"]["active"])
    for case in ({"id": "a", "status": "error", "phase": "build"},
                 {"id": "c", "status": "done", "done_at": "x", "phase": "close"}):
        ok(f"Re-run is always RENDERED (status={case['status']})", "rerun" in _acts(case))

    panels = src("web/frontend/src/features/dev/panels.tsx")
    ok("…and never appears on a CARD — a one-click button that deletes a lifecycle "
       "belongs behind the drilldown", "rerun" not in panels.lower())


# ── the wiring ──────────────────────────────────────────────────────────────────────────────────

def test_wiring() -> None:
    print("\n[wiring] destructive first, fire last — and the logs are never touched")

    body = src("superme_agent/daemon/services/rerun.py")
    ok("sessions are hard-deleted, the same act Drop performs", "_sessions.delete(" in body)
    ok("the worktree DIR is removed", "git_layer.remove_worktree(" in body)
    ok("…and the branch is not — `remove_worktree` keeps the ref by design",
       "delete_branch" not in body)
    ok("NO run, run-event or dev-activity row is deleted anywhere in the act",
       not any(k in body for k in ("delete_run", "purge_run", "delete_event", "wipe_events",
                                   "release_item_runs")))
    ok("…they are SOFT-deleted instead, stamped rather than removed",
       "discard_item_trace" in body and "discard_item_events" in body)
    ok("the inbox row stays — it is the original ask", "delete_inbox" not in body)
    ok("every destructive step happens BEFORE the fire",
       body.index("reset_work_item") < body.index("_fire("))
    ok("teardown is best-effort but the reset is not — a failed reset fires nothing",
       "nothing was reset" in body and body.index("nothing was reset") < body.index("_fire("))
    ok("the entry run is fired through the SAME dispatcher Resume uses",
       "from .resume import _fire" in body)
    ok("…except when upstreams hold it, which the scheduler owns",
       'if reset["status"] != "active"' in body)
    ok("a reset that fires nothing lands at `error`, not `active` with no run",
       "set_work_item_error(" in body)
    ok("the restart leaves a trail the owner can read", '"item.rerun"' in body)
    # Both halves pinned here because both are easy to silently lose:
    ok("the ROWS are soft-deleted, never removed",
       "discard_item_trace(" in body and "discard_item_events(" in body)
    ok("…and stamped BEFORE the `item.rerun` event, so that event survives unstamped",
       body.index("discard_item_trace(") < body.index('"item.rerun"'))
    ok("the BRANCH is re-cut, so the next attempt does not build on the failed one",
       "git_layer.recut_branch(" in body)
    ok("…after the worktree dir is gone (resetting a checked-out branch lies about its history)",
       body.index("remove_worktree(") < body.index("recut_branch("))
    ok("the trail records what was discarded and where the old commits went",
       '"runs_discarded"' in body and '"branch_backup_ref"' in body)

    route = src("superme_agent/daemon/routers/dev/work_items.py")
    ok("the route exists", '"/dev/work-items/{item_id}/rerun"' in route)
    ok("…and calls the shared service rather than tearing down itself",
       "from ...services.rerun import rerun_item" in route)
    ok("…409ing on the same conditions the button reads", "status_code=409" in route)
    ok("…and Re-run and Resume stay two routes over two services, never one flag",
       '"/dev/work-items/{item_id}/resume"' in route
       and "from ...services.resume import resume_item" in route)

    api = src("web/frontend/src/lib/api/dev.ts")
    ok("the FE has a rerun call", "export function rerunWorkItem(" in api)
    ok("…labelled destructive where a reader will see it", "DESTRUCTIVE" in api)
    ok("…and the WorkItem type carries NO generation counter (retired 2026-07-31)",
       "generation" not in api)

    modal = src("web/frontend/src/features/dev/WorkItemModal.tsx")
    ok("the drilldown asks before it deletes", "setRerunning(true)" in modal)
    ok("…naming what goes rather than only asking 'are you sure?'",
       "Re-run this item?" in modal
       and "artifacts, reports, checkpoints and sessions are" in modal
       and "cleared" in modal)
    ok("…and the header carries NO gen badge — the counter is retired",
       "gen {it.generation}" not in modal and "it.generation" not in modal)
    ok("the drilldown STAYS open after a re-run — the item is coming back to life in it",
       "setRerunning(false)" in modal and "onClose()" not in modal.split("async function rerun()")[1]
       .split("async function decideAuth")[0])


def main() -> None:
    with TemporaryDirectory() as td:
        root = Path(td)
        test_keeps_and_clears(root)
        test_entry_and_no_counter(root)
        test_soft_delete_split(root)
        test_files(root)
        test_upstreams_still_hold(root)
    test_rerun_reason()
    test_drilldown_control()
    test_wiring()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
