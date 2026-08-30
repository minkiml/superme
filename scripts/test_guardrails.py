"""The three run guardrails: a subagent cap, a stall detector, an isolated tree.

The cap must TELL the agent it was capped, because a silently narrowed sweep reads at the gate
like a complete one.

Run: PYTHONPATH=. python -m scripts.test_guardrails
"""
import re

import asyncio
import subprocess
import tempfile
from pathlib import Path

from superme_agent.core import git_layer, kernel_speech
from superme_agent.core.vocab import kind_profiles
from superme_agent.core.permissions import build_can_use_tool, deny_all
from superme_agent.daemon.services import watchdog
from superme_agent.harness.policy import MAX_SUBAGENTS, SAFE_TOOLS, SUBAGENT_TOOLS
from scripts.sources import src

PASS = 0


def ok(msg: str, cond: bool = True) -> None:
    global PASS
    assert cond, f"FAILED: {msg}"
    PASS += 1
    print(f"  ok  {msg}")


def allowed(result) -> bool:
    return type(result).__name__ == "PermissionResultAllow"


# ── 1 · the subagent cap ────────────────────────────────────────────────────────────────────────

def test_the_cap_counts_and_then_refuses():
    async def run():
        gate = build_can_use_tool(deny_all)
        results = [await gate("Agent", {"prompt": "read X"}, None)
                   for _ in range(MAX_SUBAGENTS + 3)]
        return results
    results = asyncio.run(run())
    ok(f"exactly {MAX_SUBAGENTS} spawns are allowed",
       sum(1 for r in results if allowed(r)) == MAX_SUBAGENTS)
    ok("…and every spawn after that is refused",
       all(not allowed(r) for r in results[MAX_SUBAGENTS:]))
    # A capped reader must be able to report that it was capped: silence reads as an empty
    # surface.
    msg = str(getattr(results[-1], "message", ""))
    ok("the refusal tells the agent the LIMIT is why, not that the brief was wrong",
       "limit" in msg.lower() and str(MAX_SUBAGENTS) in msg)
    ok("…and tells it to say so in its report", "report" in msg.lower())


def test_the_budget_is_per_turn():
    async def run():
        spent = build_can_use_tool(deny_all)
        for _ in range(MAX_SUBAGENTS):
            await spent("Agent", {}, None)
        fresh = build_can_use_tool(deny_all)          # a NEW turn = a new callback
        return await spent("Agent", {}, None), await fresh("Agent", {}, None)
    exhausted, new_turn = asyncio.run(run())
    ok("a spent turn cannot spawn again", not allowed(exhausted))
    ok("…but the next turn starts with a full budget", allowed(new_turn))


def test_both_spawn_names_are_covered():
    ok("`Agent` is the spawn the SDK issues today", "Agent" in SUBAGENT_TOOLS)
    ok("…and the older `Task` name counts against the same budget", "Task" in SUBAGENT_TOOLS)
    ok("the spawn is still a safe tool — capped, not gated on a human", "Agent" in SAFE_TOOLS)


def test_the_cap_is_on_by_default():
    # Default-ON where every turn's options are built: a runner that forgot it would be the run
    # nobody could stop.
    perms = src("superme_agent/core/permissions.py")
    ok("the cap defaults to the policy value, not to None",
       "subagent_cap: int | None = MAX_SUBAGENTS" in perms)
    ok("…and is checked before the safe-tool auto-allow that would otherwise pass Agent through",
       perms.index("if subagent_cap is not None") < perms.index("if is_safe(tool_name"))

    async def run():
        gate = build_can_use_tool(deny_all, subagent_cap=None)
        return [await gate("Agent", {}, None) for _ in range(MAX_SUBAGENTS + 5)]
    ok("…and an explicit None still means uncapped, for a caller that means it",
       all(allowed(r) for r in asyncio.run(run())))


# ── 2 · the stall watchdog ──────────────────────────────────────────────────────────────────────

def _run_row(minutes_quiet: float, **kw) -> dict:
    from datetime import datetime, timedelta, timezone
    quiet = datetime.now(timezone.utc) - timedelta(minutes=minutes_quiet)
    return {"id": 1, "repo_id": "r", "item_id": "i", "phase": "investigate",
            "quiet_since": quiet.isoformat(), **kw}


def test_the_stall_rule(monkeypatched=None):
    # Every fixture is a MEASURED shape: a healthy rhythm, a post-fan-out synthesis pause, and the
    # incident this exists for.
    rows = [_run_row(2 / 60), _run_row(70 / 60), _run_row(21), _run_row(24)]
    watchdog._spine.live_item_runs_quiet_since = lambda: rows      # noqa: SLF001 — the read, stubbed
    stalled = watchdog.stalled_runs()
    ok("a run mid-work (2s since its last event) is not stalled", len(stalled) == 2)
    ok("A SYNTHESIS PAUSE (70s of thinking after a fan-out) IS NOT A STALL — it was killed once",
       all(s["quiet_seconds"] > 70 for s in stalled))
    ok("…nor one just under the threshold",
       all(s["quiet_seconds"] >= watchdog.STALL_SECONDS for s in stalled))
    ok("the incident's own shape (24 minutes of silence) IS caught",
       any(s["quiet_seconds"] > 20 * 60 for s in stalled))
    ok("the threshold is far above a healthy run's rhythm and far below the incident",
       5 * 60 <= watchdog.STALL_SECONDS <= 30 * 60)
    ok("…and the SHIPPED default is that, not whatever a test env last set",
       'or 20 * 60' in src("superme_agent/daemon/services/watchdog.py"))
    ok("…and the poll is frequent enough to catch one within a threshold's grace",
       watchdog.POLL_SECONDS <= watchdog.STALL_SECONDS // 4)


def test_quiet_since_falls_back_to_the_start():
    # A run that emitted NOTHING ages from its own start, or a hung first call is invisible
    # forever.
    spine = src("superme_agent/core/spine.py")
    ok("the read coalesces the newest event with the run's start",
       "COALESCE(MAX(e.created_at), r.started_at)" in spine)
    ok("…and only item runs are watched — a chat turn has a person and a Stop button",
       "r.item_id IS NOT NULL" in spine)
    ok("a stamp that cannot be parsed never counts as a stall",
       watchdog._age_seconds("not-a-date") is None and watchdog._age_seconds(None) is None)


def test_stopping_is_cancel_then_close_then_label():
    wd = src("superme_agent/daemon/services/watchdog.py")
    cancel, close, label = (wd.index("run_tasks.cancel"), wd.index("finish_item_run"),
                            wd.index("mark_item_error"))
    ok("the task is cancelled before the run row is closed", cancel < close)
    ok("…and the row is closed before the item is labelled", close < label)
    ok("the row is closed by the WATCHDOG, not left to the cancellation to land",
       "_spine.finish_item_run" in wd)
    ok("a stall does NOT auto-resume — the cause is unknown, so a person decides",
       "resume_item" not in wd)
    ok("the stop leaves a permanent trail", "run.stalled" in wd)


def test_the_registry_is_bound_where_every_item_run_passes():
    turns = src("superme_agent/daemon/services/turns.py")
    ok("the turn runner registers its task", "run_tasks.register" in turns)
    ok("…and always releases it", "finally:" in turns and "run_tasks.release" in turns)
    from superme_agent.daemon.services import run_tasks
    ok("a turn with no item registers nothing — that is a chat turn",
       run_tasks.register("r", None) is None)
    ok("cancelling an unknown item is a no-op, not an error",
       run_tasks.cancel("r", "nobody") is False)


def test_disposal_stops_the_task_not_just_the_row():
    """A disposed item must stop RUNNING, not merely stop claiming to run.

    The row half alone let a turn outlive its item and write into a folder being removed."""
    runs = src("superme_agent/daemon/services/runs.py")
    # The CODE, not the docstring above it — which names both halves while explaining the order.
    body = runs.split("def stop_item_work", 1)[1].split('"""')[2]
    cancel, release = body.index("run_tasks.cancel"), body.index("release_item_runs")
    ok("the pairing cancels the task before releasing the rows", cancel < release)
    ok("…and releases them unconditionally — cancellation may never land",
       "freed = _spine.release_item_runs" in body)

    # Every path that DISPOSES of an item goes through the pairing, never the row half alone.
    for path, who in (("superme_agent/daemon/services/clearance.py", "clearance"),
                      ("superme_agent/daemon/routers/dev/gates.py", "abandon"),
                      ("superme_agent/daemon/services/prompt_extraction.py", "probe teardown")):
        body = src(path)
        ok(f"{who} stops the work", "stop_item_work" in body)
        ok(f"…and never reaches past it to the row half", "release_item_runs" not in body)

    # Boot is the one legal bare caller: a task cannot outlive the daemon, so nothing can be running.
    life = src("superme_agent/daemon/lifespan.py")
    ok("the startup reconciler may use the row half — the registry is empty at boot",
       "spine.release_item_runs" in life)

    from superme_agent.daemon.services import run_tasks
    ok("disposal does not shout when there is nothing to cancel — that is the normal case",
       run_tasks.cancel("r", "nobody", expect_live=False) is False)

    # THE SAFETY PROPERTY: a still-registered item would cancel its own closing run, so the loop
    # must not break.
    turns = src("superme_agent/daemon/services/turns.py")
    ok("the registration is released in the stream's finally", "finally:" in turns
       and turns.index("finally:") < turns.index("run_tasks.release"))
    consumers = runs.split("async for ev in turn.stream")[1:]
    ok("every item runner consumes the turn stream", len(consumers) >= 4)
    for i, seg in enumerate(consumers):
        body = re.split(r"\n(?:async )?def ", seg)[0]      # to the end of this runner
        ok(f"turn-stream loop {i + 1} of {len(consumers)} runs to exhaustion — no early break",
           not re.search(r"\n\s+break\b", body))


def test_the_watchdog_is_actually_started():
    life = src("superme_agent/daemon/lifespan.py")
    ok("the poll loop is launched at startup", "watchdog.watch_loop()" in life)
    ok("…and cancelled at shutdown", "stall_task.cancel()" in life)


# ── 3 · the research scratch worktree ───────────────────────────────────────────────────────────

def test_the_profile_separates_isolation_from_landing():
    research = kind_profiles.get_profile("research")
    impl = kind_profiles.get_profile("implementation")
    ok("research gets an isolated tree", research.scratch_worktree is True)
    ok("…but still lands nothing — `worktree` stays False, so review never merges it",
       research.worktree is False)
    ok("implementation is unchanged: it lands, and wants no scratch tree",
       impl.worktree is True and impl.scratch_worktree is False)


def test_every_research_phase_reads_from_the_same_tree():
    # One item, one thread, one cwd: two phases on different trees is one agent watching its own
    # paths move.
    ok("a research intake phase runs at the tree",
       kind_profiles.phase_uses_worktree("investigate", "research") is True)
    ok("an implementation intake phase still runs at the repo",
       kind_profiles.phase_uses_worktree("triage", "implementation") is False)
    ok("build/vet are unaffected", kind_profiles.phase_uses_worktree("build") is True)


def test_the_tree_is_detached_and_disposable():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        for cmd in (["init", "-b", "main"], ["config", "user.email", "t@t"],
                    ["config", "user.name", "t"]):
            subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
        (repo / "a.txt").write_text("one\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "c1"], cwd=repo, check=True, capture_output=True)

        rec = git_layer.create_scratch_worktree(repo, "testrepo", "aaaabbbbcccc")
        wt = Path(rec["worktree"])
        try:
            ok("a tree is made", wt.is_dir() and (wt / "a.txt").is_file())
            ok("it carries NO branch — nothing to merge, nothing to name", rec["branch"] is None)
            head = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=wt,
                                  capture_output=True, text=True, encoding="utf-8")
            ok("…because HEAD is detached", head.returncode != 0)
            branches = git_layer.list_branches(repo)
            ok("the repo gains no branch for it", branches == ["main"])

            again = git_layer.create_scratch_worktree(repo, "testrepo", "aaaabbbbcccc")
            ok("a second call reuses the tree rather than failing", again.get("reused") is True)

            # The reconcile must not call a branchless record broken — it is healthy by design.
            acts = git_layer.reconcile(repo, "testrepo",
                                       {"aaaabbbbcccc": {"branch": None, "worktree": str(wt)}})
            ok("startup reconciliation leaves a scratch tree alone",
               not [a for a in acts if a.get("action") == "broken"])
        finally:
            git_layer.remove_worktree(repo, "testrepo", "aaaabbbbcccc")
            # The worktrees home is OUTSIDE the temp dir, so the suite must take its own parent
            # back.
            root = git_layer.worktrees_root("testrepo")
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        ok("removal takes the directory with it", not wt.exists())
        ok("…and the suite leaves no worktrees home behind",
           not git_layer.worktrees_root("testrepo").exists())


def test_creation_and_cleanup_are_wired():
    runs = src("superme_agent/daemon/services/runs.py")
    ok("every intake run resolves its cwd through the one helper",
       "ensure_scratch_worktree" in runs and "replace(ctx, cwd=repo_dir)" in runs)
    ops = src("superme_agent/daemon/services/git_ops.py")
    ok("the helper is lazy — a sweep item minted straight at investigate never sees a gate",
       "def ensure_scratch_worktree" in ops)
    ok("…and falls back to the live repo rather than failing a run", "return ctx.cwd" in ops)
    ok("the tree is recorded as the item's git_worktree, which is what cleanup reads",
       "git_worktree=rec[\"worktree\"]" in ops)
    # CLEARANCE: the scratch tree rides the SAME path, so it cannot grow its own forgotten
    # cleanup.
    clear = src("superme_agent/daemon/services/clearance.py")
    drop = src("superme_agent/daemon/routers/dev/gates.py")
    ok("close removes it", 'item.get("git_worktree")' in clear and "remove_worktree" in clear)
    ok("drop/abandon removes it", 'item.get("git_worktree")' in drop and "remove_worktree" in drop)
    life = src("superme_agent/daemon/lifespan.py")
    ok("and a terminal item's leftover dir is swept at startup",
       "removed terminal item" in life)


def test_the_agent_is_told_where_it_is():
    item = {"id": "abc", "title": "T", "kind": "research", "phase": "investigate",
            "git_worktree": "/tmp/wt", "git_base": "main"}
    block = kernel_speech.work_item_preamble("abc", item, "/tmp/item", interactive=False)
    ok("the boundary does NOT claim this is where its code changes go",
       "all code changes happen" not in block)
    ok("it names the tree as detached and throwaway",
       "detached" in block and "throwaway" in block)
    # Both are things the agent cannot work out for itself and would get wrong in its report.
    ok("…tells it to cite repo-relative paths, not this tree's", "repo-relative" in block)
    ok("…and that it is reading the committed anchor, not the owner's working tree",
       "uncommitted" in block)
    impl = kernel_speech.work_item_preamble(
        "abc", {**item, "kind": "implementation", "phase": "build"}, "/tmp/item", interactive=False)
    ok("the build boundary is untouched", "all code changes happen" in impl)


# ── 4 · the token accumulator (parent + subagents) ──────────────────────────────────────────────

def test_the_total_counts_subagents_and_cannot_kill_a_run():
    from types import SimpleNamespace
    from superme_agent.daemon.services.runs.lifecycle import LiveTokens
    from superme_agent.daemon.services.runs import lifecycle as RN

    written: list[int] = []
    real = RN._spine
    RN._spine = SimpleNamespace(set_item_run_tokens=lambda *a, **k: written.append(k["tokens"]))
    try:
        live = LiveTokens()
        step = lambda mid, i, cc, cr, o: SimpleNamespace(  # noqa: E731
            message_id=mid, ctx_pct=None,
            usage={"input_tokens": i, "cache_creation_input_tokens": cc,
                   "cache_read_input_tokens": cr, "output_tokens": o})
        live.bump("r", "i", step("m1", 10, 100, 5000, 20))     # the parent
        live.bump("m1-dup", "i", step("m1", 10, 100, 5000, 25))  # same call, later step → replaces
        live.bump("r", "i", step("m2", 5, 50, 4000, 500))      # a SUBAGENT's call, same stream
        u = live.usage()
        ok("one entry per API call — a repeated message_id replaces, never doubles",
           u["output_tokens"] == 25 + 500)
        ok("…and a subagent's call is counted, which is the whole point",
           u["input_tokens"] == 15 and u["cache_creation_input_tokens"] == 150)
        ok("cache_read is kept separately, never dropped", u["cache_read_input_tokens"] == 9000)
        ok("the scalar excludes cache_read, as every other surface does",
           live.tokens() == 15 + 150 + 525)

        # A counter must not be able to stop the work.
        live.bump("r", "i", step("m3", [1, 2], None, "x", 7))
        ok("a nonsense usage value is counted as 0, not raised",
           live.usage()["output_tokens"] == 25 + 500 + 7)
        broken = SimpleNamespace(message_id="m4", ctx_pct=None,
                                 usage=property(lambda s: 1 / 0))   # raises on attribute access
        live.bump("r", "i", broken)
        ok("…and an outright broken step is swallowed — the run continues, uncounted", True)
    finally:
        RN._spine = real


# ── 5 · nothing about DESCRIBING the work may stop it ───────────────────────────────────────────

def test_a_trail_row_cannot_kill_a_run():
    """A malformed tool argument must not kill the run that made it.

    One raised out of the trail formatter and the run's own task, leaving the row `running`."""
    from superme_agent.daemon.services.runs import _artifact_desc, capture_event

    ok("the exact killer input now formats instead of raising",
       _artifact_desc("Read", {"file_path": "a.py", "limit": [100]})[2].endswith("[whole]"))
    ok("…and a partly-bad span keeps the half that parsed",
       _artifact_desc("Read", {"file_path": "a.py", "offset": ["x"], "limit": 2})[2]
       .endswith("[0-2]"))
    ok("a numeric STRING is still a span — agents type those",
       _artifact_desc("Read", {"file_path": "a.py", "limit": "50"})[2].endswith("[0-50]"))
    ok("a real span is unchanged",
       _artifact_desc("Read", {"file_path": "a.py", "offset": 10})[2].endswith("[10+]"))

    class Boom:                                   # an event that raises on attribute access
        tool_name = "Read"
        tool_id = parent_tool_id = None
        @property
        def tool_input(self): raise RuntimeError("boom")

    capture_event("r", Boom())                    # must return, not raise
    ok("…and capture_event swallows an event it cannot read at all", True)
    src_runs = src("superme_agent/daemon/services/runs.py")
    ok("the wall is at capture_event itself, not only at the one known shape",
       "def capture_event" in src_runs and "_capture_event(repo_id, ev" in src_runs)


def main() -> None:
    test_the_cap_counts_and_then_refuses()
    test_the_budget_is_per_turn()
    test_both_spawn_names_are_covered()
    test_the_cap_is_on_by_default()
    test_the_stall_rule()
    test_quiet_since_falls_back_to_the_start()
    test_stopping_is_cancel_then_close_then_label()
    test_the_registry_is_bound_where_every_item_run_passes()
    test_disposal_stops_the_task_not_just_the_row()
    test_the_watchdog_is_actually_started()
    test_the_profile_separates_isolation_from_landing()
    test_every_research_phase_reads_from_the_same_tree()
    test_the_tree_is_detached_and_disposable()
    test_creation_and_cleanup_are_wired()
    test_the_agent_is_told_where_it_is()
    test_the_total_counts_subagents_and_cannot_kill_a_run()
    test_a_trail_row_cannot_kill_a_run()
    print(f"\nALL GREEN — {PASS} checks passed (self-cleaned).")


if __name__ == "__main__":
    main()
