"""The runners themselves: one turn, no chat surface, driven to completion here."""

import time
from dataclasses import replace
from pathlib import Path

from ...app_state import (agent as _agent, dev as _dev, dev_store as _dev_store,
                          sessions as _sessions, spine as _spine)
from ...deps import cache_slash as _cache_slash
from ....core import (Init, Result, Status, TextDelta, ToolResult, Usage, deny_all,
                      scoped_writes_approve)
from ....core import artifacts as _arts
from ....core import autopilot as _autopilot
from ....core import git_layer, kernel_speech
from ....core.vocab import kind_profiles
from ....harness.tools.run_tools import make_run_report_server
from ..turns import ResilientTurn
from .lifecycle import LiveTokens, dev_mcp, end_run, log, mark_item_error, retry_notice
from .capture import capture_event, capture_prompt, capture_run_input, surface_from_turn
from .checkpoints import bank_auto_checkpoint, reset_vet_thread
from .completion import UNREPORTED, ensure_completion
from .close import _clear_or_retry

async def run_background_plan(ctx, context_id: str, item_id: str, item_dir: Path,
                               model: str | None = None, effort: str | None = None) -> None:
    """Background "Plan it" — one /plan turn, no surface. Thin wrapper over _background_intake_run."""
    await _background_intake_run(ctx, context_id, item_id, item_dir,
                                 skill="plan", model=model, effort=effort)


async def run_background_item_skill(ctx, context_id: str, item_id: str, item_dir: Path,
                                     skill: str, model: str | None = None,
                                     effort: str | None = None) -> None:
    """The generic phase-entry runner for any auto-fired item skill that is not plan: `review`,
    `investigate`, `itemize`.

    All carry the item's INTAKE role — one thread end to end — so only the skill differs. Thin
    wrapper over `_background_intake_run`."""
    await _background_intake_run(ctx, context_id, item_id, item_dir,
                                 skill=skill, model=model, effort=effort)


async def _run_background_triage(ctx, context_id: str, item_id: str, item_dir: Path,
                                 model: str | None = None, effort: str | None = None) -> None:
    """Auto-triage on push: one triage turn, no surface, fired when an inbox item is pushed to the
    workspace.

    The item lands at `awaiting_human` with its classification recorded, so the owner glances and
    approves."""
    await _background_intake_run(ctx, context_id, item_id, item_dir,
                                 skill="triage", model=model, effort=effort)


async def _background_intake_run(ctx, context_id: str, item_id: str, item_dir: Path, *,
                                 skill: str, model: str | None = None,
                                 effort: str | None = None) -> None:
    """Drive one background intake-phase turn with no surface attached, then clear run-state. Only the
    skill and trigger differ.

    RESUMES THIS PHASE'S OWN THREAD, or mints when it has none: re-entering a phase is one agent
    looking at a changed tree."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    # A read-only kind reads its own detached checkout; swapping here keeps every phase on one
    # cwd.
    from ..git_ops import ensure_scratch_worktree
    repo_dir = ensure_scratch_worktree(ctx, context_id, item,
                                       dev=_dev, dev_store=_dev_store, spine=_spine)
    if repo_dir != ctx.cwd:
        ctx = replace(ctx, cwd=repo_dir)
        item = _dev.read_work_item(dev_root, item_id) or item   # re-read: the git record moved
    # The phase this run IS, not `skill` — `itemize` is a research item's closing run.
    run_phase = str(item.get("phase") or "triage")
    # Resuming this phase's own thread continues where it left off; a first entry has no slot, so
    # the CLI mints.
    prev_session = item.get("session_id") or None
    # Bank before the thread dies — this runner mints a fresh session and deletes the previous
    # one.
    if prev_session:
        try:
            bank_auto_checkpoint(ctx, item_id)
        except Exception:
            log.exception("pre-replace checkpoint failed for %s", item_id)
    title = item.get("title") or item_id
    # A research worktree is a detached scratch tree — the only one this run reads or may destroy.
    wt = item.get("git_worktree")
    scratch_tree = ([Path(wt)]
                    if wt and kind_profiles.get_profile(
                        str(item.get("kind") or "implementation")).scratch_worktree
                    else [])
    # A resumed agent believes its memory over the folder, so a re-entry is told what changed.
    changed: list[str] = []
    if prev_session:
        try:
            since = _spine.last_phase_run_end(context_id, item_id, phase=run_phase)
            changed = _arts.changed_since(item_dir, since)
        except Exception:
            log.exception("re-entry delta failed for %s at %s", item_id, run_phase)
    trigger = kernel_speech.intake_trigger(skill, item_id, title, changed)
    prompt = trigger
    capture_prompt(context_id, trigger, item_id=item_id)
    focus = kernel_speech.work_item_preamble(item_id, item, str(item_dir), interactive=False)
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = LiveTokens()   # dedupes the Usage stream by message_id for an accurate live estimate
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    turn = ResilientTurn(f"background {skill}", item_id=item_id,
                         notify=retry_notice(context_id, item_id, skill))
    # Built once, then both SNAPSHOTTED and SENT — see `surface_from_turn`.
    turn_kwargs = dict(
        resume=prev_session,   # this PHASE's own thread; None the first time it is entered
        model=model,
        effort=effort or _spine.effective_effort(context_id),  # item → repo → system → medium
        approve=scoped_writes_approve(item_dir, deny_all),
        # Without a shell boundary every command the read-only classifier cannot prove goes to
        # `deny_all`, with no path to allow.
        write_boundary=[item_dir],
        # One path outside the boundary refuses the whole command, so the shell may name the
        # scratch worktree.
        shell_roots=scratch_tree,
        sandbox_writes=[item_dir, *scratch_tree],   # the kernel holds the same two roots
        extra_mcp_servers={**dev_mcp(ctx, ctx.cwd, item_id, scope=skill),
                           "run": make_run_report_server(sink)},
        system_append=focus,
        item_bound=True,       # one item is this run's subject — no board-wide in-progress list
    )
    # Throwaway probes only — capture matches the real send exactly. Normal items skip it.
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=ctx, system_append=focus, prompt=prompt,
                          phase=skill,
                          surface=surface_from_turn(turn_kwargs, mcp=["dev", "run"]),
                          background=True)
    async for ev in turn.stream(_agent, ctx, prompt, **turn_kwargs):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            # Accumulated per-message usage (parent + subagents), not the parent-only
            # `Result.usage`; falls back when no Usage step arrived.
            final_usage = live.usage(ev.usage) or ev.usage
            final_session = ev.session_id
            _sessions.record(ctx, ev.session_id)
            if ev.session_id:
                try:
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id,
                                               slot=kind_profiles.session_slot(run_phase))
                    # Reverse stamp: a background session born here gets its durable work-item
                    # identity and its spine kind.
                    _spine.stamp_session_item(ev.session_id, item_id)
                    _spine.stamp_session_kind(ev.session_id,
                                              kind_profiles.session_role(run_phase))
                except Exception:
                    log.exception("background %s: failed to persist session to %s" % (skill, item_id))
                # The replaced thread is superseded — delete it so the picker stays clean; its run
                # trace is preserved.
                if prev_session and prev_session != ev.session_id:
                    _sessions.delete(ctx, prev_session, cause="retired")
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        # Per-run trail for the Activity trace: the reply text, each call and its output, keyed to
        # this run.
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    # `ensure_completion` asks once more when a run ended undeclared; an intake phase ends at
    # someone's approval, so agents skip it.
    report = await ensure_completion(ctx, context_id, item_id, sink, skill=skill,
                                     session_id=final_session, model=model, effort=effort)
    # Finished ⇒ the item sits at the owner's gate; died ⇒ `error`, because `awaiting_human` would
    # claim a decision is wanted.
    stopped = turn.fault.failed and not report
    if stopped:
        mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase=skill)
    end_run(ctx, context_id, item_id, final_tokens,
             "error" if stopped else "awaiting_human", final_usage,
             outcome="blocked" if stopped else ((report or {}).get("outcome") or UNREPORTED),
             session_id=final_session, summary=str((report or {}).get("summary") or ""))
    # Session-end checkpoint hook: a background session ends here — bank the fallback if the
    # run didn't write its own checkpoint.
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after background %s failed", skill)
    # Itemize is research's closing run, so it owes clearance — otherwise the item rests at close
    # forever.
    if skill == "itemize" and not stopped:
        _clear_or_retry(context_id, item_id,
                        str((report or {}).get("outcome") or UNREPORTED))
    log.info("background %s: done for %s%s", skill, item_id,
             f" ({turn.fault.kind})" if turn.fault.failed else "")


async def run_background_resolve(ctx, context_id: str, item_id: str, worktree: Path,
                                conflicts: list[str], model: str | None = None,
                                  effort: str | None = None) -> None:
    """Drive one background turn that edits a conflicted merge's markers, then COMPLETE the merge
    mechanically daemon-side — the agent never commits.

    Success re-enters `vet`; failure pages the owner with the merge still in the tree."""
    dev_root = ctx.internal_root / "dev"
    # No `report_completion` mount: the outcome is mechanical (did the merge finish), never the
    # agent's claim.
    prompt = kernel_speech.resolve_trigger(worktree, item_id, conflicts)
    capture_prompt(context_id, prompt, item_id=item_id)
    final_tokens = None
    final_usage = None
    final_session = None
    run_started = time.time()
    live = LiveTokens()
    turn = ResilientTurn("background resolve", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "resolve"))
    async for ev in turn.stream(
        _agent, ctx, prompt,
        resume=None,
        model=model,
        effort=effort or _spine.effective_effort(context_id),
        approve=scoped_writes_approve(worktree, deny_all),
        sandbox_writes=[worktree],   # resolving a conflict is git + edits inside the tree, nothing more
        extra_mcp_servers=dev_mcp(ctx, worktree, item_id, scope="resolve"),  # Dev tools mounted so a background planner can read the log, roadmap and inbox.
    ):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens = ev.tokens
            # Accumulated per-message usage (parent + subagents), not the parent-only
            # `Result.usage`; falls back when no Usage step arrived.
            final_usage = live.usage(ev.usage) or ev.usage
            final_session = ev.session_id
            _sessions.record(ctx, ev.session_id)
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    # Mechanically finish the merge — ground truth (marker scan + git state), not the agent's claim.
    resolved = False
    detail = ""
    try:
        res = git_layer.finish_merge(worktree)
        resolved = True
        detail = f"merge completed at {res['commit'][:10]}"
    except git_layer.GitError as e:
        detail = str(e)
    outcome = "success" if resolved else "blocked"
    if resolved:
        item = _dev.read_work_item(dev_root, item_id) or {}
        revet = str(item.get("phase")) == "review"
        if revet:  # Re-vet before re-presenting: the merge changed the diff the owner already saw.
            reset_vet_thread(ctx, item)         # vet forgets — fresh vetter for the re-entry
            _dev.set_work_item_phase(dev_root, item_id, "vet")
            # Every phase move lands in the trail — this is the one non-gate transition.
            _dev_store.log_event(context_id, "phase.advance",
                                 "Conflict resolved — re-entering vet before re-presenting",
                                 item_id=item_id, actor="daemon",
                                 meta={"from": "review", "to": "vet"})
        end_run(ctx, context_id, item_id, final_tokens, "active", final_usage, outcome=outcome,
                 session_id=final_session)
        # Something must run behind that `active`. Fired after `end_run`, because `start_vet_run`
        # refuses while a run holds the lock.
        if revet:
            from ..loop import start_vet_run
            started, why = start_vet_run(ctx, context_id, item_id)
            if not started:
                # Never leave `active` with no run: rest it where the owner can see it instead.
                _dev.set_work_item_status(dev_root, item_id, "awaiting_human")
                log.warning("resolve: vet re-entry did not start for %s (%s)", item_id, why)
    elif turn.fault.failed:
        # The resolver never finished: an outage, not a hard conflict. The merge is still in the
        # tree either way.
        mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase="resolve")
        end_run(ctx, context_id, item_id, final_tokens, "error", final_usage,
                 outcome=outcome, session_id=final_session)
    else:
        # Conflicts remain in the tree (deliberate — retry or manual abort); page the owner.
        end_run(ctx, context_id, item_id, final_tokens, "awaiting_human", final_usage,
                 outcome=outcome, session_id=final_session)
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after resolve failed")
    _dev_store.log_event(context_id, "git.resolve",
                         f"Conflict resolution {'succeeded' if resolved else 'FAILED'}: {detail}",
                         item_id=item_id, actor="daemon", meta={"resolved": resolved})
    log.info("background resolve: %s for %s (%s)", "done" if resolved else "failed", item_id, detail)
