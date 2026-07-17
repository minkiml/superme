"""WS-S8 gate test (unit half) — compaction runtime mechanics (PRD stage S8/D11).

Covers, without a daemon or LLM: the floor-aware config guard (a trigger the incompressible
floor would exceed is refused; per-session floors RAISE the effective trigger); the trigger
decision (below-trigger / defer latch / attempts cap / back-off all block; over-trigger fires);
the pure effectiveness verdict (real pre/post tokens vs min-gain; no boundary = ineffective);
strike accumulation semantics; and the spine config round-trip with defaults. The LIVE half
(real session on the dummy repo: checkpoint-before-boundary order in the trace, real verdict
numbers, two-strike back-off → awaiting_human, cold-start from the banked checkpoint) runs
separately.

Run: PYTHONPATH=. python -m scripts.test_ws_s8
"""

import asyncio
import tempfile
from pathlib import Path

from superme_agent.daemon.services import compaction as C

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def test_floor_guard() -> None:
    print("floor-aware config guard")
    ok("trigger at the floor refused", C.validate_trigger(C.FLOOR_MIN_PCT) is not None)
    ok("trigger below the floor refused", C.validate_trigger(10) is not None)
    ok("trigger above 95 refused", C.validate_trigger(96) is not None)
    ok("sane trigger accepted", C.validate_trigger(80) is None)


def test_effective_trigger(monkey_cfg) -> None:
    print("effective trigger (session-floor raise + per-kind override)")
    monkey_cfg({"trigger_pct": 60, "by_kind": {"research": 90}, "min_gain_pct": 30})
    C._state.clear()
    ok("configured trigger when no floor observed",
       C.effective_trigger("s1", "implementation") == 60)
    C.note_fill("s1", 12)
    ok("small floor leaves the configured trigger",
       C.effective_trigger("s1", "implementation") == 60)
    C.note_fill("s2", 70)   # a fat session: floor 70 → trigger raised to 80
    ok("session floor RAISES the effective trigger past the configured one",
       C.effective_trigger("s2", "implementation") == 70 + C.SESSION_FLOOR_MARGIN)
    ok("later fills never lower the floor",
       (C.note_fill("s2", 5) or C.effective_trigger("s2", "implementation")) == 80)
    ok("per-kind override wins", C.effective_trigger("s1", "research") == 90)
    # Model switch: a floor % is only meaningful against its model's window — switching models
    # re-measures the floor from the next fill (same model never does).
    C.note_fill("s3", 70, "claude-haiku-4-5")     # 200k window: floor 70 → trigger 80
    ok("floor sticks across same-model fills",
       (C.note_fill("s3", 8, "claude-haiku-4-5") or C.effective_trigger("s3", None)) == 80)
    C.note_fill("s3", 8, "claude-sonnet-5")        # 1M window: same content ≈ 8% → re-measured
    ok("model switch RE-MEASURES the session floor",
       C.effective_trigger("s3", None) == 60)


def test_trigger_decision(monkey_cfg) -> None:
    print("trigger decision: latch, attempts cap, back-off")
    monkey_cfg({"trigger_pct": 50, "by_kind": {}, "min_gain_pct": 30})
    C._state.clear()
    fired: list[str] = []

    async def fake_run(ctx, context_id, item_id, session_id, *, model, pre_pct):
        fired.append(session_id)
        return {}

    real = C.run_compaction
    C.run_compaction = fake_run  # type: ignore[assignment]
    try:
        async def scenario():
            ctx = object()
            ok("below trigger → no fire",
               C.maybe_compact(ctx, "r", "i", "sA", ctx_pct=30, kind=None, model=None) is False)
            ok("over trigger → fires",
               C.maybe_compact(ctx, "r", "i", "sA", ctx_pct=60, kind=None, model=None) is True)
            await asyncio.sleep(0)
            # run_compaction (the real one) sets attempts+defer; the fake didn't — set manually
            # to assert each guard independently.
            st = C._s("sA")
            st.defer = True
            ok("defer latch blocks re-fire until the next real turn",
               C.maybe_compact(ctx, "r", "i", "sA", ctx_pct=90, kind=None, model=None) is False)
            C.note_turn_start("sA")
            st.attempts = C.ATTEMPTS_PER_TURN
            ok("attempts/turn cap blocks",
               C.maybe_compact(ctx, "r", "i", "sA", ctx_pct=90, kind=None, model=None) is False)
            C.note_turn_start("sA")
            ok("note_turn_start clears latch + attempts (fires again)",
               C.maybe_compact(ctx, "r", "i", "sA", ctx_pct=90, kind=None, model=None) is True)
            await asyncio.sleep(0)
            st.backed_off = True
            C.note_turn_start("sA")
            ok("back-off is permanent for the session",
               C.maybe_compact(ctx, "r", "i", "sA", ctx_pct=99, kind=None, model=None) is False)
            ok("no fill measurement → never fires",
               C.maybe_compact(ctx, "r", "i", "sB", ctx_pct=None, kind=None, model=None) is False)
        asyncio.run(scenario())
        ok("exactly the two expected compactions fired", fired == ["sA", "sA"])
    finally:
        C.run_compaction = real  # type: ignore[assignment]


def test_verdict() -> None:
    print("effectiveness verdict (pure)")
    v = C.judge_effectiveness({"preTokens": 100_000, "postTokens": 20_000}, 30)
    ok("manual: real shrink past threshold = effective",
       v["effective"] and v["gain_pct"] == 80.0 and v["pre_tokens"] == 100_000
       and v["mode"] == "manual")
    v = C.judge_effectiveness({"preTokens": 100_000, "postTokens": 90_000}, 30)
    ok("manual: 10% shrink under a 30% threshold = STRIKE", not v["effective"])
    ok("no boundary recorded = ineffective",
       not C.judge_effectiveness(None, 30)["effective"]
       and not C.judge_effectiveness({}, "auto")["effective"])
    # AUTO: judged against RECLAIMABLE (pre − floor), not the total. A preload-heavy session
    # (floor 80k of 100k) shrinking to 85k reclaimed 15k of 20k = 75% of reclaimable → effective
    # — the flat 30% rule would have branded this perfect-ish compaction a failure (gain 15%).
    v = C.judge_effectiveness({"preTokens": 100_000, "postTokens": 85_000}, "auto",
                              floor_tokens=80_000)
    ok("auto: preload-heavy session judged by reclaimable, not flat %",
       v["effective"] and v["mode"] == "auto" and v["reclaimable"] == 20_000
       and v["reclaimed_ratio"] == 0.75)
    # A bloated session (floor 5k) shrinking 100k→60k reclaimed 40k of 95k = 42% → STRIKE,
    # even though its flat gain (40%) would have passed the old 30% rule.
    v = C.judge_effectiveness({"preTokens": 100_000, "postTokens": 60_000}, "auto",
                              floor_tokens=5_000)
    ok("auto: bloated session's mediocre reclaim = STRIKE despite 40% flat gain",
       not v["effective"] and v["reclaimed_ratio"] < C.AUTO_RECLAIM_FRACTION)
    v = C.judge_effectiveness({"preTokens": 100_000, "postTokens": 20_000}, "auto")
    ok("auto without a floor measurement falls back to the flat threshold",
       v["mode"] == "auto-fallback" and v["effective"])


def test_config_roundtrip(tmp: Path) -> None:
    print("spine config round-trip")
    from superme_agent.core.spine import SystemSpine
    sp = SystemSpine(db_path=tmp / "s8.db")
    cfg = sp.get_compaction_config()
    ok("defaults (min_gain defaults to auto)",
       cfg == {"trigger_pct": 80, "by_kind": {}, "min_gain_pct": "auto"})
    sp.set_compaction_config(trigger_pct=66, by_kind={"research": 88})
    cfg = sp.get_compaction_config()
    ok("partial set persists (min_gain untouched)",
       cfg == {"trigger_pct": 66, "by_kind": {"research": 88}, "min_gain_pct": "auto"})
    sp.set_compaction_config(min_gain_pct=50)
    ok("second partial set keeps the rest",
       sp.get_compaction_config() == {"trigger_pct": 66, "by_kind": {"research": 88},
                                      "min_gain_pct": 50})
    sp.set_compaction_config(min_gain_pct="auto")
    ok("manual → auto round-trips", sp.get_compaction_config()["min_gain_pct"] == "auto")


def main() -> None:
    # Route effective_trigger's config read at a stub so no daemon spine is touched.
    cfg_holder = {}

    def monkey_cfg(cfg: dict) -> None:
        cfg_holder.clear()
        cfg_holder.update(cfg)

    real_get = C._spine.get_compaction_config
    C._spine.get_compaction_config = lambda: dict(cfg_holder)  # type: ignore[method-assign]
    try:
        with tempfile.TemporaryDirectory() as td:
            test_floor_guard()
            test_effective_trigger(monkey_cfg)
            test_trigger_decision(monkey_cfg)
            test_verdict()
            test_config_roundtrip(Path(td))
    finally:
        C._spine.get_compaction_config = real_get  # type: ignore[method-assign]
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
