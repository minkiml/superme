"""Compaction runtime mechanics, without a daemon or an LLM.

A trigger the incompressible floor would exceed is refused, and a per-session floor raises the
effective one. The effectiveness verdict is pure: no boundary means ineffective.

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
    print("effective trigger (configured value + per-kind override, nothing else)")
    monkey_cfg({"trigger_pct": 60, "by_kind": {"research": 90}, "min_gain_pct": 30})
    C._state.clear()
    ok("the configured trigger IS the trigger", C.effective_trigger("implementation") == 60)
    ok("per-kind override wins", C.effective_trigger("research") == 90)
    ok("unknown kind falls back to the default profile's trigger",
       C.effective_trigger(None) == 60)
    # An observed fill must never move the trigger.
    ok("no session state can move the trigger", not hasattr(C, "SESSION_FLOOR_MARGIN")
       and not hasattr(C, "note_fill"))


def test_trigger_decision(monkey_cfg, fills: dict) -> None:
    print("trigger decision at run start: latch, back-off, no reading")
    monkey_cfg({"trigger_pct": 50, "by_kind": {}, "min_gain_pct": 30})
    C._state.clear()
    fills.clear()
    ok("below trigger → not due", (fills.update(sA=30) or C.due("sA", None)) is None)
    ok("over trigger → due, and reports the deciding fill",
       (fills.update(sA=60) or C.due("sA", None)) == 60)
    C._s("sA").defer = True
    ok("defer latch blocks until the next real turn",
       (fills.update(sA=90) or C.due("sA", None)) is None)
    C.note_turn_start("sA")
    ok("note_turn_start releases the latch", C.due("sA", None) == 90)
    C._s("sA").backed_off = True
    C.note_turn_start("sA")
    ok("back-off is permanent for the session", C.due("sA", None) is None)
    ok("no fill recorded for the session → never due", C.due("sB", None) is None)
    ok("no session at all → never due", C.due(None, None) is None)


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
    # Judged against RECLAIMABLE with both sides on one basis: a preload-heavy floor is not shed.
    v = C.judge_effectiveness({"preTokens": 100_000, "postTokens": 5_000}, "auto",
                              floor_tokens=10_000)
    ok("auto: judged by reclaimable, post restated onto the floor-inclusive basis",
       v["effective"] and v["mode"] == "auto" and v["reclaimable"] == 90_000
       and v["post_tokens_with_floor"] == 15_000 and v["reclaimed_ratio"] == 0.94)
    # A bloated session's floor is not shed, so a flat gain that passes the old rule still
    # strikes.
    v = C.judge_effectiveness({"preTokens": 100_000, "postTokens": 60_000}, "auto",
                              floor_tokens=5_000)
    ok("auto: bloated session's mediocre reclaim = STRIKE despite 40% flat gain",
       not v["effective"] and v["reclaimed_ratio"] < C.AUTO_RECLAIM_FRACTION)
    # A ratio above 1.0 is proof the two numbers were not comparable.
    v = C.judge_effectiveness({"preTokens": 120_661, "postTokens": 12_025}, "auto",
                              floor_tokens=21_297)
    ok("auto: the real 2026-07-28 compaction no longer scores above 1.0",
       v["effective"] and v["reclaimed_ratio"] == 0.88)
    v = C.judge_effectiveness({"preTokens": 100_000, "postTokens": 20_000}, "auto")
    ok("auto without a floor measurement falls back to the flat threshold",
       v["mode"] == "auto-fallback" and v["effective"])


def test_role_scoped_checkpoints(tmp: Path) -> None:
    """Three threads bank into one folder — continuity must read ITS OWN, or it hands a compacted
    intake thread the build thread's state as recovered memory."""
    print("checkpoints — role stamp + role-scoped read")
    from superme_agent.core import artifacts as A
    d = tmp / "item"
    (d / "checkpoints").mkdir(parents=True)
    # An UNSTAMPED file first (pre-stamp era) — it must stay visible to every role.
    (d / "checkpoints" / "20260101-000000.md").write_text(
        "---\ncheckpoint: legacy\n---\n## Working on\nold\n", encoding="utf-8")
    ok("unstamped is visible to a role read",
       (A.latest_checkpoint(d, role="intake") or {}).get("path", "").endswith("000000.md"))
    A.write_checkpoint(d, None, working_on="intake w", decisions="", remaining="r",
                       role="intake")
    A.write_checkpoint(d, None, working_on="build w", decisions="", remaining="r", role="build")
    newest = A.latest_checkpoint(d)
    ok("role=None still returns the newest from ANY thread (the item-state read)",
       "build w" in (newest or {})["text"])
    ok("role='intake' skips the newer BUILD checkpoint",
       "intake w" in (A.latest_checkpoint(d, role="intake") or {})["text"])
    ok("role='build' gets its own", "build w" in (A.latest_checkpoint(d, role="build") or {})["text"])
    ok("the stamp is in the frontmatter",
       "\nrole: build\n" in Path((A.latest_checkpoint(d, role="build") or {})["path"]).read_text(encoding="utf-8"))
    ok("a role with nothing of its own and no legacy file reads None",
       A.latest_checkpoint(tmp / "empty", role="vet") is None)


def test_compaction_notice() -> None:
    """The post-compaction pointer: present only when owed, and a POINTER — never a body."""
    print("post-compaction notice")
    from superme_agent.core import kernel_speech as KS
    ok("no checkpoint → no notice", KS.compaction_notice(None) == ""
       and KS.compaction_notice("") == "")
    n = KS.compaction_notice("/i/checkpoints/20260101-000000.md")
    ok("names the path", "/i/checkpoints/20260101-000000.md" in n)
    ok("says the memory is a summary, not the thing",
       "SUMMARY" in n and "compacted" in n)
    ok("carries the latest-message-wins envelope we can't put on /compact's own output",
       "latest message always wins" in n)
    ok("stays small — a pointer, not a file dump", len(n) < 700)
    item = {"id": "i", "title": "T", "kind": "implementation", "phase": "plan"}
    with_n = KS.work_item_preamble("i", item, "/i", compacted_checkpoint="/i/c.md")
    without = KS.work_item_preamble("i", item, "/i")
    ok("the preamble carries it only when a checkpoint is owed",
       "This thread was compacted" in with_n and "This thread was compacted" not in without)


def test_session_memory(tmp: Path) -> None:
    """A session with no work-item has no checkpoint folder and no artifacts, so
    `session-memory/<sid>.md` is the ONLY thing that survives its compaction."""
    print("session memory (general sessions)")
    from superme_agent.core import artifacts as A
    root = tmp / "know" / "dev"
    ok("no memory yet reads None", A.read_session_memory(root, "sess-1") is None)
    p = A.session_memory_path(root, "sess-1")
    ok("the path is derived from the session id — nothing to store a pointer in",
       p.as_posix().endswith("dev/session-memory/sess-1.md"))
    # The AGENT writes this file: a general session has no item tools, so there is no kernel
    # writer.
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("## Working on\nw1\n\n## Decisions\n—\n\n## Remaining\nr1\n\n## Notes\n—\n", encoding="utf-8")
    got = A.read_session_memory(root, "sess-1")
    ok("...and the read finds it", got and got["path"] == str(p) and "w1" in got["text"])
    ok("another session's memory is a different file",
       A.read_session_memory(root, "sess-2") is None)
    ok("nothing in the kernel writes it — a derived fallback cannot exist for a general session",
       not hasattr(A, "write_session_memory"))
    long = "x" * 9000
    p.write_text(long, encoding="utf-8")
    ok("the read is char-capped like every other artifact read",
       len(A.read_session_memory(root, "sess-1", char_cap=100)["text"]) == 100
       and A.read_session_memory(root, "sess-1", char_cap=100)["truncated"])
    # A general session has no item folder, so the notice must not point at one.
    from superme_agent.core import kernel_speech as KS
    gen = KS.compaction_notice(str(p), has_artifacts=False)
    ok("general notice drops the item-artifacts fallback",
       "item's artifacts" not in gen and "only record of this thread" in gen)
    ok("...and still carries the envelope + the path",
       "latest message always wins" in gen and str(p) in gen)


def test_forced_trigger(monkey_cfg, fills: dict) -> None:
    """The owner's manual "compact now" rides the SAME decision, threshold bypassed."""
    print("manual compaction (force)")
    monkey_cfg({"trigger_pct": 90, "by_kind": {}, "min_gain_pct": 30})
    C._state.clear()
    fills.clear()
    fills.update(sM=12)
    ok("well under the trigger → auto says no", C.due("sM", None) is None)
    ok("forced fires anyway, reporting the real fill", C.due("sM", None, force=True) == 12)
    C._s("sM").defer = True
    ok("forced overrides the defer latch (the owner asked twice on purpose)",
       C.due("sM", None, force=True) == 12)
    C._s("sM").backed_off = True
    ok("but NOT the back-off — a session proven uncompactable stays that way",
       C.due("sM", None, force=True) is None)
    C._state.clear()
    ok("forced with no reading at all returns 0, not None — callers must test `is None`",
       C.due("sNew", None, force=True) == 0)


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
    # Route the spine reads at stubs, so no daemon spine and no real run history is touched.
    cfg_holder: dict = {}
    fills: dict = {}

    def monkey_cfg(cfg: dict) -> None:
        cfg_holder.clear()
        cfg_holder.update(cfg)

    real_get = C._spine.get_compaction_config
    real_pct = C._spine.session_ctx_pct
    C._spine.get_compaction_config = lambda: dict(cfg_holder)  # type: ignore[method-assign]
    C._spine.session_ctx_pct = lambda sid: fills.get(sid)      # type: ignore[method-assign]
    try:
        with tempfile.TemporaryDirectory() as td:
            test_floor_guard()
            test_effective_trigger(monkey_cfg)
            test_trigger_decision(monkey_cfg, fills)
            test_verdict()
            test_role_scoped_checkpoints(Path(td))
            test_compaction_notice()
            test_session_memory(Path(td))
            test_forced_trigger(monkey_cfg, fills)
            test_config_roundtrip(Path(td))
    finally:
        C._spine.get_compaction_config = real_get  # type: ignore[method-assign]
        C._spine.session_ctx_pct = real_pct        # type: ignore[method-assign]
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
