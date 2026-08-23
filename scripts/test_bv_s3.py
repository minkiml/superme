"""Session kinds: the explicit phase-to-session map.

Intake narrates, build remembers, vet forgets. The map is total over both kinds' pipelines and
fails loudly off it, and the intake thread survives build entry.

Run: PYTHONPATH=. python -m scripts.test_bv_s3
"""

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from superme_agent.core import artifacts as _arts
from superme_agent.core import kernel_speech
from superme_agent.core.vocab import kind_profiles as KP
from superme_agent.core.dev_knowledge import DevKnowledgeService
from superme_agent.core.sessions import _is_noise, _preset_title
from superme_agent.core.spine import SystemSpine
from superme_agent.daemon.routers.ws import resolve_item_session
from scripts.sources import src

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok  {name}")


def test_role_map() -> None:
    print("role map (§1.3)")
    # Research has no fresh-perspective boundary, so its whole life is one intake thread.
    want = {"triage": "intake", "plan": "intake", "review": "intake", "close": "intake",
            "build": "build", "vet": "vet", "investigate": "intake"}
    ok("every pipeline phase has a role",
       all(KP.session_role(p) == want[p] for prof in KP.KIND_PROFILES.values() for p in prof.phases))
    ok("map matches the design table", {p: KP.session_role(p) for p in want} == want)
    ok("null phase defaults to triage/intake", KP.session_role(None) == "intake")
    try:
        KP.session_role("deliver")   # the pre-rename name must be DEAD, not silently intake
        ok("unknown phase fails loud", False)
    except KeyError:
        ok("unknown phase fails loud", True)
    ok("worktree cwd for build+vet only",
       [KP.role_uses_worktree(r) for r in KP.SESSION_ROLES] == [False, True, True])


def test_slots(tmp: Path) -> None:
    print("item.md per-phase slots + computed session_id")
    dev = DevKnowledgeService()
    root = tmp / "devroot"
    wid = dev.create_work_item(root, "s3 item", kind="implementation")["id"]

    it = dev.read_work_item(root, wid)
    ok("fresh item: no slots, no computed sid",
       it["sessions"] == {} and it["session_id"] is None)

    ok("unknown slot rejected",
       _raises(lambda: dev.set_work_item_session(root, wid, "x", slot="deliver")))
    ok("the RETIRED shared intake slot can never be written again",
       _raises(lambda: dev.set_work_item_session(root, wid, "x", slot="intake")))

    dev.set_work_item_session(root, wid, "sid-triage", slot="triage")
    it = dev.read_work_item(root, wid)
    ok("triage slot written + computed at triage",
       it["sessions"] == {"triage": "sid-triage"} and it["session_id"] == "sid-triage")

    # THE RULE, first half: a DIFFERENT phase mints — it does not inherit the last phase's thread.
    dev.set_work_item_phase(root, wid, "plan")
    it = dev.read_work_item(root, wid)
    ok("at plan: computed sid is None (mint), triage's thread SURVIVES untouched",
       it["session_id"] is None and it["sessions"]["triage"] == "sid-triage")
    dev.set_work_item_session(root, wid, "sid-plan", slot="plan")

    # THE step-3 claim still holds: entering build does not lose the intake-side threads.
    dev.set_work_item_phase(root, wid, "build")
    ok("at build: mint, and both intake-side threads survive",
       dev.read_work_item(root, wid)["session_id"] is None)
    dev.set_work_item_session(root, wid, "sid-build", slot="build")
    dev.set_work_item_phase(root, wid, "vet")
    ok("at vet: fresh mint (no vet slot yet)", dev.read_work_item(root, wid)["session_id"] is None)
    dev.set_work_item_session(root, wid, "sid-vet", slot="vet")

    dev.set_work_item_phase(root, wid, "review")
    ok("at review: MINTS — review never held a thread before, and does not adopt plan's",
       dev.read_work_item(root, wid)["session_id"] is None)
    dev.set_work_item_session(root, wid, "sid-review", slot="review")

    # THE RULE, second half: the same phase resumes its own thread, because a revise round sends
    # the item back and forward.
    dev.set_work_item_phase(root, wid, "plan")
    ok("back at plan (a revise round): plan's OWN thread returns",
       dev.read_work_item(root, wid)["session_id"] == "sid-plan")
    dev.set_work_item_phase(root, wid, "review")
    ok("forward to review again: review's OWN thread returns, not plan's",
       dev.read_work_item(root, wid)["session_id"] == "sid-review")

    dev.set_work_item_phase(root, wid, "close")
    ok("at close: mints (its own slot, still empty)",
       dev.read_work_item(root, wid)["session_id"] is None)

    ok("unchanged slot write skipped",
       dev.set_work_item_session(root, wid, "sid-vet", slot="vet") is False)
    it = dev.read_work_item(root, wid)
    ok("work_item_session_ids sees every thread",
       set(dev.work_item_session_ids(it))
       == {"sid-triage", "sid-plan", "sid-build", "sid-vet", "sid-review"})

    # read_all mirrors the single-item read.
    ra = next(x for x in dev.read_all(root)["work_items"] if x["id"] == wid)
    ok("read_all mirrors slots + computed sid",
       ra["sessions"] == dev.read_work_item(root, wid)["sessions"]
       and ra["session_id"] is None)


def test_legacy(tmp: Path) -> None:
    print("legacy fallbacks: bare session_id + the retired shared intake slot")
    dev = DevKnowledgeService()
    root = tmp / "devroot-legacy"
    wid = dev.create_work_item(root, "legacy item", kind="implementation")["id"]
    md = root / "work-items" / wid / "item.md"
    md.write_text(md.read_text(encoding="utf-8").replace("session_id: null", 'session_id: "sid-legacy"'), encoding="utf-8")

    it = dev.read_work_item(root, wid)
    ok("legacy sid feeds the computed session_id",
       it["session_id"] == "sid-legacy" and it["sessions"] == {})
    dev.set_work_item_phase(root, wid, "build")
    ok("…at ANY phase while no slot covers it",
       dev.read_work_item(root, wid)["session_id"] == "sid-legacy")

    # Writing any slot hands ownership to the slots: legacy must stop shadowing other phases.
    dev.set_work_item_session(root, wid, "sid-build", slot="build")
    it = dev.read_work_item(root, wid)
    ok("slot write NULLs the legacy key",
       "session_id: null" in md.read_text(encoding="utf-8") and it["session_id"] == "sid-build")
    dev.set_work_item_phase(root, wid, "review")
    ok("no stale legacy shadow at review (fresh mint)",
       dev.read_work_item(root, wid)["session_id"] is None)

    # An item carrying the retired shared slot keeps its thread at every intake phase, and loses
    # it once a real slot is written.
    root2 = tmp / "devroot-preslit"
    wid2 = dev.create_work_item(root2, "pre-split item", kind="implementation")["id"]
    md2 = root2 / "work-items" / wid2 / "item.md"
    md2.write_text(md2.read_text(encoding="utf-8").replace(
        "created_at:", 'session_intake: "sid-shared"\ncreated_at:', 1), encoding="utf-8")
    for ph in ("triage", "plan", "review", "close"):
        dev.set_work_item_phase(root2, wid2, ph)
        ok(f"pre-split item keeps its thread at {ph}",
           dev.read_work_item(root2, wid2)["session_id"] == "sid-shared")
    dev.set_work_item_phase(root2, wid2, "build")
    ok("…but NOT at build — that was never the shared slot's thread",
       dev.read_work_item(root2, wid2)["session_id"] is None)

    dev.set_work_item_phase(root2, wid2, "review")
    dev.set_work_item_session(root2, wid2, "sid-review", slot="review")
    ok("writing a real slot retires the shared one, so the adoption happens once",
       "session_intake: null" in md2.read_text(encoding="utf-8")
       and dev.read_work_item(root2, wid2)["session_id"] == "sid-review")


def test_resolve(tmp: Path) -> None:
    print("resolve_item_session (ws decision function)")
    repo, wt = tmp / "repo", tmp / "wt"
    repo.mkdir(), wt.mkdir()
    rows = {"s-int": {"cwd": str(repo)}, "s-bld": {"cwd": str(wt)}, "s-gone": None}
    adopted: list[tuple] = []
    kw = dict(worktree=wt, repo_dir=repo, get_session=lambda s: rows.get(s),
              adopt=lambda s, r: adopted.append((s, r)))

    ok("slot hit: the current PHASE's slot wins",
       resolve_item_session({"phase": "build", "sessions": {"build": "s-bld"}}, **kw)
       == ("build", "s-bld"))
    ok("empty slot: mint (None)",
       resolve_item_session({"phase": "vet", "sessions": {"build": "s-bld"}}, **kw)
       == ("vet", None))
    ok("another phase's slot never leaks into the turn",
       resolve_item_session({"phase": "review", "sessions": {"build": "s-bld"}}, **kw)
       == ("review", None) and not adopted)
    ok("…and neither does a sibling INTAKE phase's — review does not inherit plan's thread",
       resolve_item_session({"phase": "review", "sessions": {"plan": "s-int"}}, **kw)
       == ("review", None) and not adopted)

    # Pre-split adoption: the retired shared `intake` slot becomes THIS phase's, exactly once.
    ok("pre-split shared slot adopted into the current phase and resumed",
       resolve_item_session({"phase": "review", "sessions": {"intake": "s-int"}}, **kw)
       == ("review", "s-int") and adopted == [("s-int", "review")])
    adopted.clear()

    # Legacy adoption: cwd ⇒ slot; a matching slot also resumes it.
    ok("legacy repo-cwd sid adopted into the current phase and resumed at plan",
       resolve_item_session({"phase": "plan", "sessions": {}, "session_id": "s-int"}, **kw)
       == ("plan", "s-int") and adopted == [("s-int", "plan")])
    adopted.clear()
    ok("legacy worktree-cwd sid adopted as build and resumed at build",
       resolve_item_session({"phase": "build", "sessions": {}, "session_id": "s-bld"}, **kw)
       == ("build", "s-bld") and adopted == [("s-bld", "build")])
    adopted.clear()
    ok("a repo-cwd legacy sid is NOT filed under build — the CLI would refuse that cwd",
       resolve_item_session({"phase": "build", "sessions": {}, "session_id": "s-int"}, **kw)
       == ("build", None) and not adopted)
    ok("unresolvable legacy cwd: left alone, mint",
       resolve_item_session({"phase": "plan", "sessions": {}, "session_id": "s-gone"}, **kw)
       == ("plan", None) and not adopted)
    ok("once any slot exists the legacy path is dead",
       resolve_item_session({"phase": "vet", "sessions": {"intake": "s-int"},
                             "session_id": "s-bld"}, **kw) == ("vet", None) and not adopted)


def test_reentry_delta(tmp: Path) -> None:
    """The other half of the per-phase-session rule.

    Resuming gives the phase its own memory; this stops that memory OUTRANKING the disk. A review
    resuming over a rewritten investigation would otherwise report that nothing had changed."""
    print("a re-entered phase is told what changed since IT last ran")
    sp = SystemSpine(db_path=tmp / "reentry.db")
    repo, wid = "r-reentry", "abc123def456"

    ok("a phase that never finished a run has no cutoff",
       sp.last_phase_run_end(repo, wid, phase="review") is None)
    rid = sp.start_item_run(repo, item_id=wid, phase="investigate")
    ok("...and neither does one still in flight — the asking run is never its own cutoff",
       rid is not None and sp.last_phase_run_end(repo, wid, phase="investigate") is None)
    sp.finish_item_run(repo, wid)
    since = sp.last_phase_run_end(repo, wid, phase="investigate")
    ok("a finished run at the phase IS the cutoff", bool(since))
    ok("the cutoff is per-PHASE, not per-item",
       sp.last_phase_run_end(repo, wid, phase="review") is None)

    # The disk half. mtimes are set explicitly so the assertion is about the comparison, not about
    # how fast this suite runs.
    item = tmp / "item"
    (item / "artifacts").mkdir(parents=True)
    (item / "reports").mkdir()
    (item / "checkpoints").mkdir()
    cut = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = cut.timestamp()
    for rel, delta in (("artifacts/investigation.md", +60), ("reports/report-investigate.md", +30),
                       ("artifacts/brief.md", -600), ("checkpoints/20260813-000000.md", +60)):
        p = item / rel
        p.write_text("x\n", encoding="utf-8")
        os.utime(p, (stamp + delta, stamp + delta))
    got = _arts.changed_since(item, cut.isoformat(timespec="seconds"))
    ok("only records written AFTER the cutoff are named",
       got == ["artifacts/investigation.md", "reports/report-investigate.md"], got)
    ok("...newest first — the last thing written is the thing most likely missed",
       got[0] == "artifacts/investigation.md")
    ok("checkpoints are out of scope (every run writes one, including this one)",
       not any(g.startswith("checkpoints/") for g in got))
    ok("no cutoff ⇒ no claim about what moved", _arts.changed_since(item, None) == [])
    ok("...and an unparseable one is not silently treated as epoch",
       _arts.changed_since(item, "whenever") == [])

    # The trigger: silence for a first entry, named files for a re-entry. "Be careful" is neither.
    plain = kernel_speech.intake_trigger("review", wid, "T")
    ok("first entry: the thin trigger, unchanged", plain.strip().endswith('("T").')
       and "\n" not in plain)
    ok("nothing moved ⇒ same thin trigger", kernel_speech.intake_trigger("review", wid, "T", []) == plain)
    re_entry = kernel_speech.intake_trigger("review", wid, "T", got)
    ok("re-entry names every changed file",
       all(f"`{g}`" in re_entry for g in got))
    ok("...and orders a re-read rather than asking for care",
       "Re-read every one of them" in re_entry)
    ok("...and says the thread's memory is the stale part",
       "stale by definition" in re_entry)
    ok("...and covers the case where the agent's OWN record was revised under it",
       "do not conclude that nothing changed" in re_entry)
    ok("the trigger is still dropped from replay (prefix match survives the appended block)",
       _is_noise({}, kernel_speech.intake_trigger("plan", wid, "T", got)))
    # EVERY skill the intake runner can fire, or the owner opens that session and reads the
    # kernel's order as their own first message.
    for sk in ("triage", "plan", "investigate", "review", "close", "itemize"):
        ok(f"…and so is `{sk}`'s — no phase shows its trigger as the owner's words",
           _is_noise({}, kernel_speech.intake_trigger(sk, wid, "T"))
           and _is_noise({}, kernel_speech.intake_trigger(sk, wid, "T", got)))
    many = [f"artifacts/a{i}.md" for i in range(15)]
    ok("a long list is capped and SAYS it was capped",
       kernel_speech.intake_trigger("plan", wid, "T", many).count("- `artifacts/a") == 12
       and "…and 3 more" in kernel_speech.intake_trigger("plan", wid, "T", many))

    # The wiring: the runner computes this only for a thread it is RESUMING.
    runs_src = src("superme_agent/daemon/services/runs.py")
    guard = runs_src.split("    changed: list[str] = []", 1)[-1].split("trigger =", 1)[0]
    ok("the intake runner asks only when it has a prior thread",
       guard.lstrip().startswith("if prev_session:"))
    ok("...against THIS phase's clock, not the item's",
       "last_phase_run_end(context_id, item_id, phase=run_phase)" in guard)
    ok("...and hands the delta to the trigger",
       "intake_trigger(skill, item_id, title, changed)" in runs_src)


def test_titles() -> None:
    print("preset titles carry the role")
    ok("role-stamped title", _preset_title("build", "abc123def456", None, "s") ==
       "Work-item · abc123def456 · build")
    ok("legacy work_item title unchanged",
       _preset_title("work_item", "abc123def456", None, "s") == "Work-item · abc123def456")
    ok("roles are durable SESSION_KINDS", set(KP.SESSION_ROLES) <=
       set(__import__("superme_agent.core.vocab.kind_profiles", fromlist=["SESSION_KINDS"]).SESSION_KINDS))


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_role_map()
        test_slots(tmp)
        test_legacy(tmp)
        test_resolve(tmp)
        test_reentry_delta(tmp)
        test_titles()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
