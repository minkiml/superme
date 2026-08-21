"""Sub-agent attribution in the execution trace.

Four SuperMe phase skills delegate: `plan` fans out Explore readers, `investigate` one subagent per
question, `vet` one per check (haiku), `retrofit`/`project-init` one per subsystem. Those children's
tool calls arrive on the PARENT's event stream, interleaved — so a trail that drops the SDK's
`parent_tool_use_id` renders three parallel readers as one confused agent, and a "why did this run
fail" diagnosis cannot tell whose Bash was denied.

This suite pins the whole path: the SDK field survives translation (`Status`/`ToolResult`), both
trails store and return it (`run_event` for Activity/diagnosis, `run_artifact` for the work-item),
the archived `execution.md` and the diagnosis-tool render indent a child under its spawn, the FE
pairing hands the row a `depth`, and an Agent call with no recognizable type key falls back to its
DESCRIPTION rather than the useless literal "agent".

Self-cleaning (tempdir spine). No daemon needed.

Run: PYTHONPATH=. python -m scripts.test_subagent_trace
"""

import re
from pathlib import Path
from tempfile import TemporaryDirectory

from superme_agent.core import spine as spine_mod
from superme_agent.core.events import Status, ToolResult
from superme_agent.daemon.services import runs as runs_svc

PASS = 0
ROOT = Path(__file__).resolve().parents[1]


def _spine_at(db: Path) -> spine_mod.SystemSpine:
    """A throwaway spine on a tempdir DB. The config paths are the real ones (read-only here) —
    only the DB is redirected, so nothing this suite writes touches the live system."""
    return spine_mod.SystemSpine(db_path=db)


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


# ── the event contract ──────────────────────────────────────────────────────────────────────────

def test_events_carry_the_field() -> None:
    print("Status / ToolResult carry the spawn they came from")
    s = Status("Read", {"file_path": "a.py"}, tool_id="t1", parent_tool_id="spawn_1")
    ok("a Status can name its parent spawn", s.parent_tool_id == "spawn_1")
    ok("...and defaults to None (the parent's own call)",
       Status("Read", {}).parent_tool_id is None)
    r = ToolResult("Read", "body", tool_id="t1", parent_tool_id="spawn_1")
    ok("a ToolResult carries it too — a result comes back INSIDE the sub-agent that asked",
       r.parent_tool_id == "spawn_1")
    ok("...and defaults to None", ToolResult("Read", "body").parent_tool_id is None)
    # The translation seam: the SDK field is read off the MESSAGE, not the block, in both branches.
    src = (ROOT / "superme_agent/core/agent_service.py").read_text()
    ok("the turn loop reads `parent_tool_use_id` for tool-use blocks",
       "parent_tuid = getattr(message, \"parent_tool_use_id\", None)" in src
       and "parent_tool_id=parent_tuid" in src)
    ok("...and for tool results as well",
       src.count('getattr(message, "parent_tool_use_id", None)') >= 2)


# ── the label ───────────────────────────────────────────────────────────────────────────────────

def test_spawn_label() -> None:
    print("an Agent spawn always reads `Subagent`, and NAMES the worker when it can")
    kind, head, detail = runs_svc._artifact_desc("Agent", {"subagent_type": "superme-dev:capture"})
    ok("a named worker goes in parentheses",
       (kind, head, detail) == ("subagent", "Agent", "Subagent (superme-dev:capture)"))
    ok("the legacy `Task` tool name maps identically",
       runs_svc._artifact_desc("Task", {"subagent_type": "Explore"})[2] == "Subagent (Explore)")
    # The real defect this replaced: 2 of 24 recorded spawns had no type key and read "Agent - agent"
    # — a row saying an agent ran an agent.
    _, _, d = runs_svc._artifact_desc("Agent", {"description": "Map how the loop driver fires"})
    ok("no type key → the spawn's own description names the work",
       d == "Subagent (Map how the loop driver fires)")
    _, _, d2 = runs_svc._artifact_desc("Agent", {})
    ok("...and with nothing at all it still says a SUB-AGENT ran, never 'agent'", d2 == "Subagent")
    _, _, long = runs_svc._artifact_desc("Agent", {"description": "x" * 200})
    ok("the inner text is capped (a description is a sentence, not a payload)", len(long) <= 60)
    # `vet` fans out on haiku and `plan` on sonnet BY SKILL INSTRUCTION. Recording the override is
    # what makes "was the instruction followed" answerable from the trail rather than from faith.
    _, _, m = runs_svc._artifact_desc("Agent", {"subagent_type": "Explore", "model": "haiku"})
    ok("a model override rides along inside the parens", m == "Subagent (Explore · haiku)")
    _, _, nom = runs_svc._artifact_desc("Agent", {"subagent_type": "Explore"})
    ok("...and no override adds no noise", nom == "Subagent (Explore)")


# ── both trails ─────────────────────────────────────────────────────────────────────────────────

def test_trails_round_trip(tmp: Path) -> None:
    print("both trails store the attribution and hand it back")
    sp = _spine_at(tmp / "s.db")
    rid = sp.start_run("repo1", feature="build", item_id="itemA")

    # run_event — the Activity / diagnosis trail.
    sp.log_run_event(repo_id="repo1", kind="subagent", name="Agent", description="Explore",
                     run_id=rid, item_id="itemA", tool_id="spawn_1")
    sp.log_run_event(repo_id="repo1", kind="tool", name="Read", description="a.py",
                     run_id=rid, item_id="itemA", tool_id="t1", parent_tool_id="spawn_1")
    sp.log_run_event(repo_id="repo1", kind="tool", name="Bash", description="pytest",
                     run_id=rid, item_id="itemA", tool_id="t2")
    ev = sp.events_for_run(rid)
    by_tool = {e["tool_id"]: e for e in ev}
    ok("the spawn row itself has no parent (it IS the parent's call)",
       by_tool["spawn_1"]["parent_tool_id"] is None)
    ok("the child's call names its spawn", by_tool["t1"]["parent_tool_id"] == "spawn_1")
    ok("the parent's own later call does not", by_tool["t2"]["parent_tool_id"] is None)

    # …and the item-scoped read (the drilldown's Runs pane) sees the same rows.
    arts = {a["tool_id"]: a for a in sp.events_for_item("repo1", "itemA")}
    ok("the item-scoped read carries it too", arts["t1"]["parent_tool_id"] == "spawn_1")
    ok("...and the spawn stays unparented", arts["spawn_1"]["parent_tool_id"] is None)
    ok("the column is additive — pre-existing rows read back as None (no migration needed)",
       all("parent_tool_id" in a for a in arts.values()))


def test_capture_path_threads_it(tmp: Path) -> None:
    """The capture functions are the ONLY writers; a field the events carry but capture drops is a
    field that does not exist. Assert at the seam, not at the dataclass."""
    print("the capture path threads it from event to row")
    sp = _spine_at(tmp / "s2.db")
    rid = sp.start_run("repo2", feature="vet", item_id="itemB")
    runs_svc._spine = sp   # the service module holds a module-level spine
    runs_svc.capture_event("repo2", Status("Read", {"file_path": "x.py"}, tool_id="c1",
                                           parent_tool_id="spawn_9"),
                           run_id=rid, item_id="itemB", publish_live=False)
    runs_svc.capture_event("repo2", ToolResult("Read", "out", tool_id="c1",
                                              parent_tool_id="spawn_9"),
                           run_id=rid, item_id="itemB", publish_live=False)
    rows = {(e["kind"], e["tool_id"]): e for e in sp.events_for_run(rid)}
    ok("a captured sub-agent CALL keeps its spawn",
       rows[("tool", "c1")]["parent_tool_id"] == "spawn_9")
    ok("...and so does its RESULT (else the pair splits across nesting levels)",
       rows[("result", "c1")]["parent_tool_id"] == "spawn_9")


# ── the renders ─────────────────────────────────────────────────────────────────────────────────

def test_renders_indent() -> None:
    print("every render nests the child under its spawn")
    md = (ROOT / "superme_agent/daemon/services/runs.py").read_text()
    ok("the archived execution.md indents a parented row",
       'indent = "    " if a.get("parent_tool_id") else ""' in md)
    diag = (ROOT / "superme_agent/harness/tools/dev_tools.py").read_text()
    ok("the diagnosis-tool trace indents it too — whose call was denied IS the diagnosis",
       'if e.get("parent_tool_id")' in diag)
    trace = (ROOT / "web/frontend/src/lib/trace.ts").read_text()
    ok("the FE pairing hands each row a depth off the same field",
       "depth: e.parent_tool_id ? 1 : 0" in trace)
    ok("...and depth is typed as the two levels it is, not an open number",
       "depth: 0 | 1" in trace)
    rows = (ROOT / "web/frontend/src/features/dev/ExecutionTrace.tsx").read_text()
    ok("a nested row is visually indented behind a rail",
       "const nested = depth === 1" in rows and "border-l border-line" in rows)
    ok("numbering stays continuous across levels (the order is the point)",
       "start + i" in rows)


# ── the Trace tab's two panes ───────────────────────────────────────────────────────────────────

def test_trace_tab_panes() -> None:
    print("Trace is two peer panes, and the Timeline is not a window")
    modal = (ROOT / "web/frontend/src/features/dev/WorkItemModal.tsx").read_text()
    ok("Runs and Timeline are addressable subs of the Trace tab",
       "{ id: 'runs', label: 'Runs' }" in modal and "{ id: 'timeline', label: 'Timeline' }" in modal)
    ok("...wired into the tab's sub list", "tab === 'trace' ? TRACE_SUBS" in modal)
    ok("the heavy call-trail is only fetched by the pane that shows it",
       "pane === 'runs' ? K.itemArtifacts" in modal)
    ok("the item's event feed is no longer capped at 50",
       "limit: EVENT_CAP" in modal and "limit: 50 }" not in modal)
    ok("...and a cap that IS reached is stated rather than read as 'that's all'",
       "events.length >= EVENT_CAP" in modal)
    router = (ROOT / "web/frontend/src/lib/router/index.ts").read_text()
    ok("both subs are in the router's closed vocabulary",
       "'runs', 'timeline'" in router)


def test_runs_pane_sees_every_run(tmp: Path) -> None:
    """The Runs pane groups by RUN, so a run that recorded no CALL must still appear — otherwise
    the pane silently answers "this item had 17 runs" when it had 34. The live case that exposed
    it: every `deputy` run (20+ real calls, written to run_event only) and every `compact` run."""
    print("the item's Runs pane sees every run, and the duplicate trail is retired")
    sp = _spine_at(tmp / "s3.db")
    # One live run per item is a DB invariant (idx_run_one_live), so these are sequential — which is
    # also the real shape: build finishes, then the deputy judges what it produced.
    build = sp.start_run("repoX", feature="build", item_id="itemC")
    sp.log_run_event(repo_id="repoX", kind="tool", name="Read", run_id=build, item_id="itemC")
    sp.finish_run(build, tokens=0)
    # A deputy run was NEVER written to run_artifact — that is what made the pane lose it.
    deputy = sp.start_run("repoX", feature="deputy", item_id="itemC")
    sp.log_run_event(repo_id="repoX", kind="mcp", name="deputy_verdict", run_id=deputy,
                     item_id="itemC")
    sp.log_run_event(repo_id="repoX", kind="reply", name="reply", description="approved",
                     run_id=deputy, item_id="itemC")

    ok("the duplicate writer is gone — one trail, not two",
       not hasattr(sp, "log_artifact") and not hasattr(sp, "artifacts_for_item"))
    new = {e["run_id"] for e in sp.events_for_item("repoX", "itemC")}
    ok("the event trail has BOTH runs, including the deputy's", new == {build, deputy})
    rows = sp.events_for_item("repoX", "itemC")
    ok("newest run first, calls in order within it — same ordering the pane already assumed",
       rows[0]["run_id"] == deputy)
    ok("text rows ride along so a text-only run still forms a group",
       any(r["kind"] == "reply" for r in rows))
    from superme_agent.daemon.schemas.common import ArtifactKind
    ok("...which the wire type must allow, or the whole response 500s",
       "prompt" in ArtifactKind.__args__ and "reply" in ArtifactKind.__args__)
    modal = (ROOT / "web/frontend/src/features/dev/WorkItemModal.tsx").read_text()
    # A run number alone says nothing about what ran. The header names the FEATURE in words, and
    # a chat turn is further qualified by the phase it happened in — 'chat' is the one kind that
    # occurs in every phase, so the phase is what tells two of them apart.
    ok("the group header names WHAT the run was", "RUN_KIND" in modal
       and "deputy: 'deputy judgment'" in modal
       and "meta?.feature === 'chat' && meta.phase ? `${meta.phase}:${kind}`" in modal)
    ok("...and a run with no calls says so rather than rendering an empty group",
       "only exchanged text" in modal)


def test_compacted_build_thread_reloads_its_skill() -> None:
    """Build REMEMBERS: cycle 1 invokes `superme-dev:build`, later cycles resume that thread with
    the procedure already in the transcript and skip the call. A compaction can cut the procedure
    away — and the unchanged "Run superme-dev:build" line will not make an agent re-read something
    it believes it already has. So the trigger must say what CHANGED."""
    print("a compacted build thread is told to re-invoke its skill")
    from superme_agent.core.kernel_speech import build_loop_trigger
    normal = build_loop_trigger("it1", "T", 2, "…report…")
    ok("the ordinary cycle trigger is unchanged",
       "Run superme-dev:build to fix" in normal and "COMPACTED" not in normal)
    after = build_loop_trigger("it1", "T", 2, "…report…", reload_skill=True)
    ok("after a compaction it names the cause and asks for the skill again",
       "COMPACTED" in after and "invoke the `superme-dev:build` skill again" in after)
    ok("...and still carries the work order", "--- build-vet-2.md ---" in after)
    src = (ROOT / "superme_agent/daemon/services/loop.py").read_text()
    ok("the runner derives the flag from the SAME signal the checkpoint pointer uses",
       "compacted = compacted_checkpoint(ctx, item, prev_build)" in src
       and "reload_skill=bool(compacted)" in src)


def test_api_error_is_a_fault_not_a_success() -> None:
    """Build run 804 (2026-07-30): the whole trail was `prompt` + one "API Error: 529 Overloaded",
    and it was stamped `outcome=success`, then VETTED. The SDK surfaces that as assistant text, not
    an exception, so `turn_error` stayed False and the cycle read as a clean advance."""
    print("an API error that arrives as text is a fault, not a successful cycle")
    # R1 moved this judgment out of loop.py into the ONE classifier every runner shares
    # (core/faults.py, exercised in depth by scripts/test_faults.py). What this suite still owns is
    # the loop-side consequence: whatever the classifier says, an empty cycle must not advance.
    from superme_agent.core.faults import classify
    from superme_agent.daemon.services.loop import decide_after_build
    ok("the SDK's own error line is recognized",
       classify(reply="API Error: 529 Overloaded. This is a server-side issue").failed
       and classify(reply="  api error 500 ").failed)
    ok("an agent WRITING about an error is not one",
       not classify(reply="The 500 we saw earlier came from the stub server").failed
       and not classify(reply="Fixed: the API Error: 429 path now retries").failed)
    ok("empty text is not a fault",
       not classify(reply="").failed and not classify(reply=None).failed)
    ok("a turn that called a tool is judged by its work, not its prose",
       not classify(reply="API Error: 529 Overloaded", did_work=True).failed)
    live = {"status": "active", "phase": "build"}
    ok("classified as infra, it reaches the retry ladder instead of advancing",
       decide_after_build(live, outcome=None, turn_error=True)["klass"] == "infra")
    ok("...while a normal no-report cycle still advances (the loop never pages mid-build)",
       decide_after_build(live, outcome=None, turn_error=False)["klass"] == "advance")
    src = (ROOT / "superme_agent/daemon/services/loop.py").read_text()
    ok("the build runner reads its verdict off the shared classifier",
       "turn_error = turn.fault.failed" in src)


def main() -> None:
    test_events_carry_the_field()
    test_spawn_label()
    with TemporaryDirectory() as td:
        test_trails_round_trip(Path(td))
        test_capture_path_threads_it(Path(td))
        test_runs_pane_sees_every_run(Path(td))
    test_compacted_build_thread_reloads_its_skill()
    test_api_error_is_a_fault_not_a_success()
    test_renders_indent()
    test_trace_tab_panes()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
