"""Thread-2 gate test — auto-triage on push (#120) + the always-stop-but-trivial triage gate.

Covers, offline (no daemon, no tokens):
  · The triage runner — `_run_background_triage` is a thin wrapper that drives `_background_intake_run`
    with `skill="triage"` (shared intake-phase driver, same one `_run_background_plan` uses).
  · Auto-fire on push — `_fire_auto_triage` opens a `triage` run (phase-stamped) and schedules the
    background triage; it is BEST-EFFORT: no internal root, an already-running item, or a resolve
    failure never raises (the push must still succeed) and never double-fires.
  · The triage skill carries a "## Background runs" section that mandates
    `set_triage_classification` (the stamp that lifts the gate).
  · Replay hygiene — the triage trigger phrase is a `_NOISE_PREFIXES` entry, so it's stripped from
    the session transcript like the plan trigger.
  · FE — no stale "triage happens in chat" claim survives on the board surface.

Self-cleaning (no db needed; stubs + source assertions). Run:
PYTHONPATH=. python -m scripts.test_thread2
"""

import asyncio
import re
import types
from pathlib import Path
from types import SimpleNamespace

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# ------------------------------------------------------------------ triage runner = intake driver
def test_triage_runner_delegates() -> None:
    print("triage runner — thin wrapper over the shared intake-phase driver")
    from superme_agent.daemon.services import runs

    seen = {}

    async def _fake_intake(ctx, context_id, item_id, item_dir, *, skill, model=None, effort=None):
        seen.update(skill=skill, item_id=item_id, model=model, effort=effort)

    orig = runs._background_intake_run
    try:
        runs._background_intake_run = _fake_intake
        asyncio.run(runs._run_background_triage(
            SimpleNamespace(), "global", "itemX", Path("/tmp/x"), "opus", "high"))
        ok("triage runner drives _background_intake_run(skill='triage')", seen.get("skill") == "triage",
           str(seen))
        ok("it forwards item + model + effort",
           seen.get("item_id") == "itemX" and seen.get("model") == "opus" and seen.get("effort") == "high",
           str(seen))
        # And the plan wrapper still delegates with skill='plan' (shared driver, unchanged behavior).
        seen.clear()
        asyncio.run(runs._run_background_plan(SimpleNamespace(), "global", "itemP", Path("/tmp/p")))
        ok("plan runner still drives skill='plan'", seen.get("skill") == "plan", str(seen))
    finally:
        runs._background_intake_run = orig


# ------------------------------------------------------------------ auto-fire on push
def test_fire_auto_triage() -> None:
    print("fire_auto_triage — opens a triage run + schedules the background pass, best-effort")
    from tempfile import TemporaryDirectory
    from superme_agent.daemon.services import runs as RN
    from superme_agent.gateway import contexts as GW

    ITEM = ("---\nid: i1\ntitle: T\nkind: implementation\nstatus: {status}\nphase: {phase}\n"
            "created_at: 2026-07-17T00:00:00Z\nupdated_at: 2026-07-17T00:00:00Z\n---\nprobe\n")
    spine = SimpleNamespace(effective_model=lambda *a, **k: "m",
                            effective_effort=lambda *a, **k: "medium")
    begins: list[tuple] = []
    tasks: list = []

    def _stub_begin(ctx, context_id, item_id, kind, model, phase=None):
        begins.append((kind, phase, model))
        return 1  # a fresh run id

    def _rec_task(coro):
        tasks.append(coro)
        coro.close()  # we only assert it was scheduled — don't actually run it

    async def _fake_triage(*a, **k):
        return None

    orig = (GW.resolve, RN._begin_run, RN._run_background_triage, RN.asyncio)
    with TemporaryDirectory() as td:
        root = Path(td)
        d = root / "dev" / "work-items" / "i1"
        d.mkdir(parents=True)

        def write(status="active", phase="triage"):
            (d / "item.md").write_text(ITEM.format(status=status, phase=phase))

        try:
            RN._begin_run = _stub_begin
            RN._run_background_triage = _fake_triage
            RN.asyncio = types.SimpleNamespace(create_task=_rec_task)
            GW.resolve = lambda cid, mode: SimpleNamespace(internal_root=root, mode="dev", id=cid)

            write()
            ok("a fresh active item at triage fires", RN.fire_auto_triage("global", "i1", spine))
            ok("opens a run keyed `triage` in the `triage` phase",
               begins == [("triage", "triage", "m")], str(begins))
            ok("schedules exactly one background triage task", len(tasks) == 1, str(len(tasks)))

            # Past triage → never re-fires (the kick is the FIRST push only).
            begins.clear(); tasks.clear()
            write(phase="build")
            ok("an item past triage is not kicked",
               not RN.fire_auto_triage("global", "i1", spine) and tasks == [])

            # Already running — _begin_run returns None → no task, no raise.
            begins.clear(); tasks.clear()
            write()
            RN._begin_run = lambda *a, **k: None
            ok("an in-flight run doesn't double-fire",
               not RN.fire_auto_triage("global", "i1", spine) and tasks == [])

            # No internal root — nothing fires, no raise (the push still succeeds).
            begins.clear(); tasks.clear()
            RN._begin_run = _stub_begin
            GW.resolve = lambda cid, mode: SimpleNamespace(internal_root=None, mode="dev", id=cid)
            ok("no internal root → no run opened, no raise",
               not RN.fire_auto_triage("global", "i1", spine) and begins == [] and tasks == [])

            # Resolve blows up — swallowed (best-effort; the push already committed).
            begins.clear(); tasks.clear()

            def _boom(*a, **k):
                raise RuntimeError("resolve failed")
            GW.resolve = _boom
            ok("a resolve failure is swallowed",
               not RN.fire_auto_triage("global", "i1", spine) and tasks == [])
        finally:
            GW.resolve, RN._begin_run, RN._run_background_triage, RN.asyncio = orig


# ------------------------------------------------------------------ skill + replay + FE
def test_skill_and_sources() -> None:
    print("skill background section + replay hygiene + FE comment")
    skill = Path("superme_agent/harness/plugins/superme-dev/skills/triage/SKILL.md").read_text()
    # Thread 3 retired the per-skill "## Background runs" narration; the delta that mattered — the
    # classification stamp that lifts the gate — stayed, and is what this asserts.
    ok("triage skill mandates set_triage_classification (the gate stamp)",
       "set_triage_classification" in skill)

    from superme_agent.core import sessions
    ok("triage trigger is a NOISE prefix",
       "Run superme-dev:triage for work-item" in sessions._NOISE_PREFIXES)
    ok("_is_noise strips the triage trigger",
       sessions._is_noise({}, 'Run superme-dev:triage for work-item `x` ("t")'))
    ok("_is_noise still strips the plan trigger",
       sessions._is_noise({}, 'Run superme-dev:plan for work-item `x` ("t")'))

    # The comment lived on the FE `isPlannable` helper, deleted 2026-07-31 with the helper. The
    # claim it guarded — triage fires on push, not in chat — is asserted at its real owner above.
    panels = _norm(Path("web/frontend/src/features/dev/panels.tsx").read_text())
    ok("the FE keeps no stale 'triage happens in chat' claim", "triage happens in chat" not in panels)


def main() -> None:
    test_triage_runner_delegates()
    test_fire_auto_triage()
    test_skill_and_sources()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
