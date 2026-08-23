"""Surface fixes: the phase guard, the run's session, the Activity column, the board's reflow.

A plan run fired outside the plan phase burned tokens on a self-blocking skill, so the rule has
one owner and it is server-side.

Run: PYTHONPATH=. python -m scripts.test_batch1
"""

import asyncio
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace

from superme_agent.core.spine import SystemSpine
from scripts.sources import src

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# ------------------------------------------------------------------ Plan-it phase guard
def test_plan_phase_guard(tmp: Path) -> None:
    print("Plan-it phase guard — /plan 409s outside `plan`")
    from fastapi import HTTPException
    from superme_agent.daemon.routers.dev import work_items as WI

    real_contexts = WI.contexts
    items: dict[str, dict] = {}
    internal = tmp / "pg-internal"
    (internal / "dev" / "work-items" / "i1").mkdir(parents=True, exist_ok=True)
    (internal / "dev" / "work-items" / "i1" / "item.md").write_text("---\nid: i1\n---\n", encoding="utf-8")
    ctx = SimpleNamespace(internal_root=internal, cwd=tmp / "pg-repo", id="t", mode="dev")
    stub_dev = SimpleNamespace(read_work_item=lambda _root, iid: items.get(iid),
                               set_work_item_model=lambda *a, **k: None,
                               set_work_item_effort=lambda *a, **k: None)
    began = {"n": 0}
    stub_spine = SimpleNamespace(
        effective_model=lambda *a, **k: "m", effective_effort=lambda *a, **k: "medium")

    try:
        WI.contexts = SimpleNamespace(resolve=lambda cid, mode: ctx)
        # begin_run must NEVER be reached outside `plan` (the whole point: refuse pre-token-burn).
        orig_begin = WI.begin_run
        WI.begin_run = lambda *a, **k: (began.__setitem__("n", began["n"] + 1) or 1)

        def run(iid: str):
            return asyncio.run(WI.dev_work_item_run(
                iid, WI.PlanBody(context_id="t"), dev=stub_dev, spine=stub_spine))

        # One door, same invariant: the route must refuse BEFORE opening a run, so a refusal costs
        # zero tokens.
        import superme_agent.daemon.services.resume as RES
        real_resolve = RES.contexts.resolve
        RES.contexts.resolve = lambda cid, mode: ctx
        real_dev, real_spine = RES._dev, RES._spine
        RES._dev = stub_dev
        RES._spine = SimpleNamespace(is_item_running=lambda *a, **k: False,
                                     effective_model=lambda *a, **k: "m",
                                     effective_effort=lambda *a, **k: "medium")
        try:
            for status, why in (("done", "terminal"), ("error", "Resume"),
                                ("awaiting_human", "waiting on your decision")):
                items["i1"] = {"id": "i1", "phase": "build", "status": status}
                try:
                    run("i1")
                    ok(f"run on a `{status}` item → 409", False)
                except HTTPException as e:
                    ok(f"run on a `{status}` item → 409",
                       e.status_code == 409 and why in str(e.detail), str(e.detail))
            ok("no run was opened for any refused state", began["n"] == 0)
        finally:
            RES.contexts.resolve, RES._dev, RES._spine = real_resolve, real_dev, real_spine
            WI.begin_run = orig_begin
    finally:
        WI.contexts = real_contexts

    # The FE no longer decides this: a second writer of the plannable rule, rendered by nothing.
    # One rule, one owner.
    dd = _norm(src("superme_agent/daemon/services/drilldown.py"))
    ok("the launch rule lives server-side, and it is ONE control",
       'runnable = bool(RUNNABLE_PHASES & {phase})' in dd and '_act("run"' in dd)
    ok("...and the three buttons it replaced are gone",
       '_act("plan"' not in dd and '_act("vet"' not in dd and '_act("force"' not in dd)
    fe = _norm(src("web/frontend/src/features/dev/panels.tsx"))
    ok("...and the FE keeps no copy of the rule", "isPlannable" not in fe)


# ------------------------------------------------------------------ run.session_id + fate
def test_run_session_id(tmp: Path) -> None:
    print("run.session_id — item runs join the session_fate path")
    spine = SystemSpine(db_path=tmp / "s.db",
                        system_config=tmp / "sys.yaml", repos_config=tmp / "repos.yaml")
    # An item run finished WITHOUT a session_id stays NULL (the old bug) — unlabelable.
    rid0 = spine.start_item_run("r", item_id="itemA", feature="chat", phase="triage")
    assert rid0
    spine.finish_item_run("r", "itemA", fallback_tokens=10)
    row0 = spine.run_history("r")[0]
    ok("finish without session_id leaves it NULL", row0.get("session_id") is None, str(row0))

    # WITH a session_id, the row carries it — and a session delete now labels it.
    spine.record_session("sess-xyz", cwd=str(tmp), surface="headless")
    rid = spine.start_item_run("r", item_id="itemB", feature="build", phase="build")
    assert rid
    spine.finish_item_run("r", "itemB", fallback_tokens=20, session_id="sess-xyz")
    rowB = next(x for x in spine.run_history("r") if x["item_id"] == "itemB")
    ok("finish_item_run(session_id=…) attaches it", rowB.get("session_id") == "sess-xyz", str(rowB))
    ok("run row starts unlabelled", rowB.get("session_fate") is None)

    existed = spine.delete_session_record("sess-xyz", cause="retired")
    ok("session delete reports the row existed", existed is True)
    rowB2 = next(x for x in spine.run_history("r") if x["item_id"] == "itemB")
    ok("session_fate now reaches the item run row", rowB2.get("session_fate") == "retired",
       str(rowB2))
    # The trace is preserved — the run row still exists after the session is gone.
    ok("run row preserved after session delete", rowB2.get("tokens") == 20)

    # Schema carries `phase`, and run_history surfaces it (the Activity column's data).
    from superme_agent.daemon.schemas.system import RunRow
    ok("RunRow schema has `phase`", "phase" in RunRow.model_fields)
    ok("run_history surfaces phase", rowB2.get("phase") == "build", str(rowB2))


# ------------------------------------------------------------------ FE surface assertions
def test_fe_surfaces() -> None:
    print("FE surfaces — Activity phase column + card layout")
    act = _norm(src("web/frontend/src/features/activity/GlobalActivity.tsx"))
    ok("Activity renders run.phase", "{r.phase &&" in act and "r.phase}" in act)
    panels = _norm(src("web/frontend/src/features/dev/panels.tsx"))
    # The board REFLOWS rather than scrolling sideways, which hides content behind an unadvertised
    # gesture.
    ok("board reflows its lanes rather than growing a sideways scrollbar",
       "minmax(0, 1fr)" in panels and "boardW >= 716 ? 4 : boardW >= 552 ? 2 : 1" in panels
       and "overflow-x-auto" not in panels)
    ok("card title truncates to one line (4-row card spec)",
       'className="truncate text-[12.5px] leading-snug text-fg"' in panels)

    # A pane that cannot fit its content must SHED, and what it sheds must stay reachable.
    layout = _norm(src("web/frontend/src/lib/layout.ts"))
    ok("a container is measured by a CALLBACK ref, so one that mounts late is still measured",
       "export function useContainerWidth<T extends HTMLElement>(): [(node: T | null) => void, number]" in layout)
    act_src = _norm(src("web/frontend/src/features/activity/GlobalActivity.tsx"))
    panels_src = _norm(src("web/frontend/src/features/dev/panels.tsx"))
    ok("every flexible run-table column can shrink to zero, so nothing is clipped off the right",
       all(t in act_src for t in (
           "grid-cols-[minmax(0,1.4fr)_72px_48px_minmax(0,1fr)_84px_64px_112px]",
           "grid-cols-[minmax(0,1.4fr)_72px_minmax(0,1fr)_84px_112px]",
           "grid-cols-[20px_minmax(0,1fr)_60px_52px]")))
    ok("the tightest table drops the repo NAME and keeps its mark",
       "{density !== 'tight' && (" in act_src)
    ok("...and trades the timestamp for an age, with the stamp kept in the tooltip",
       "density === 'tight' ? fmtAge(r.started_at) : fmtLocal(r.started_at)" in act_src)
    cfg = _norm(src("web/frontend/src/features/config/SystemConfig.tsx"))
    ok("System config measures ITSELF and collapses its rail to icons before the pane suffers",
       "useContainerWidth" in cfg and "const railIcons" in cfg and "const railNarrow" in cfg)
    ok("...and the project picker moves into the pane when the rail cannot hold it",
       "{railIcons && PROJECT_SECTIONS.has(section) && picker && (" in cfg)
    ctl = _norm(src("web/frontend/src/features/config/controls.tsx"))
    ok("a setting's control wraps below its label rather than crushing it",
       "flex flex-wrap items-center gap-x-4 gap-y-2" in ctl and "min-w-[9rem] flex-1" in ctl)

    # Narrow surfaces SIMPLIFY: words go, the figure and the actionable mark stay. Every dropped
    # word survives elsewhere.
    ok("a card in a narrow lane says fill, spend and age, and drops the labels + the model",
       "const tightCards = laneW > 0 && laneW < 215" in panels
       and "{!tight && (model || ctx != null) && (" in panels
       and "{!tight && (researchKindLabel(it.research_kind) || workKindLabel(it.kind)) && (" in panels)
    dash = _norm(src("web/frontend/src/features/dev/DevDashboard.tsx"))
    ok("the attention feed keeps WHICH item and drops the reason when there is no room",
       "{!tight && <span className=\"ml-1.5 text-[10px] text-faint\">{r.reason}</span>}" in dash)
    ok("the inbox-to-workspace arrow turns with the layout instead of pointing at white space",
       "const down = w > 0 && w < 460" in dash and "<Connector label=\"push\" down={down} />" in dash)
    ok("the work-item stat row becomes five marks + five numbers on one line",
       "const chip = (Icon: LucideIcon" in dash and "if (tight) {" in dash)
    # A picker shows the value IN FORCE: an inherit row beside it is one answer with two labels.
    pset = _norm(src("web/frontend/src/features/config/sections/ProjectSettings.tsx"))
    gen = _norm(src("web/frontend/src/features/config/sections/General.tsx"))
    # Asserted on the option LISTS, since a check its own rationale trips reads as a bug.
    ok("no config picker offers a Default row beside the value it defaults to",
       all("const MODEL_OPTS = MODEL_CATALOG.map" in src
           and "const EFFORT_OPTS = EFFORT_CATALOG.map" in src
           and "options={MODEL_OPTS}" in src
           and "value: '', label: `Default" not in src for src in (pset, gen)))
    ok("...an unset one starts at what it already runs, not at blank",
       "toModelKey(repo.modelOverride) || fallbackModel" in pset
       and "toModelKey(repo.vetModel) || fallbackModel" in pset
       and "toModelKey(sys.deputy_model) || dFallbackModel" in gen)

    ok("the run roles are a list, and the Setting tab renders it",
       "export const RUN_ROLES = [" in panels_src
       and "{RUN_ROLES.map((r) => {" in panels_src
       and "export const roleField = (role: RunRole" in panels_src)
    ok("...one grid renders both the editable and the read-only view",
       "function RoleGrid({ draft, onSet }" in panels_src
       and "<RoleGrid draft={d} onSet={set} />" in panels_src
       and "<RoleGrid draft={saved} />" in panels_src)
    ok("...and each role's picker starts from ITS OWN chain, not the item's model",
       "vet: { model: toModelKey(repo?.vet_model)" in panels_src
       and "deputy: { model: toModelKey(sys?.deputy_effective_model)" in panels_src)

    ok("Activity has no manual refresh — the feed is live and refetches on focus",
       "RefreshCw" not in act_src and "title=\"Refresh\"" not in act_src)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_plan_phase_guard(tmp)
        test_run_session_id(tmp)
        test_fe_surfaces()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
