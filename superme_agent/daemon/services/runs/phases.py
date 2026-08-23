"""Firing the next phase, and routing a gate's send-back to the phase that owns it."""

import asyncio
import time
from dataclasses import replace
from pathlib import Path

from ...app_state import (agent as _agent, dev as _dev, dev_store as _dev_store,
                          sessions as _sessions, spine as _spine)
from ...deps import cache_slash as _cache_slash
from .. import run_tasks
from ....core import (Init, Result, Status, TextDelta, ToolResult, Usage, deny_all,
                      scoped_writes_approve)
from ....core import kernel_speech
from ....core.vocab import kind_profiles
from ....harness.tools.run_tools import make_run_report_server
from ..turns import ResilientTurn
from .lifecycle import (LiveTokens, begin_run, dev_mcp, end_run, log, mark_item_error,
                        retry_notice)
from .capture import capture_event, capture_prompt
from .checkpoints import bank_auto_checkpoint, compacted_checkpoint
from .completion import UNREPORTED, ensure_completion
from .background import run_background_item_skill, _run_background_triage

def fire_review_entry(context_id: str, item_id: str, spine) -> bool:
    """Fire the review-entry run for an item that just landed at review.

    Flips the item `active` while it runs, because `active` with no run is an idle stall."""
    from ....gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if not item or str(item.get("phase")) != "review" or item.get("done_at"):
            return False
        if str(item.get("status")) not in ("active", "awaiting_human"):
            return False   # paused / parked — the owner's hold wins
        model = spine.effective_model(context_id, item_model=item.get("model"))
        effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
        if begin_run(ctx, context_id, item_id, "review", model, phase="review") is None:
            return False   # a run is already in flight — don't double-fire
        _dev.set_work_item_status(dev_root, item_id, "active")
        run_tasks.track(asyncio.create_task(
            run_background_item_skill(ctx, context_id, item_id,
                                       dev_root / "work-items" / item_id, "review", model, effort)))
        return True
    except Exception:
        log.exception("review-entry run failed to start for %s", item_id)
        try:
            _dev.set_work_item_status(ctx.internal_root / "dev", item_id, "awaiting_human")
        except Exception:
            pass
        return False


def fire_first_investigate(context_id: str, item_id: str, spine) -> bool:
    """Kick a button-launched sweep into its first investigate run.

    An item created by pressing a button should not wait for a second click."""
    from ....gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if str(item.get("status")) != "active" or str(item.get("phase")) != "investigate":
            return False
        model = spine.effective_model(context_id, item_model=item.get("model"))
        effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
        if begin_run(ctx, context_id, item_id, "investigate", model, phase="investigate") is None:
            return False   # a run is already in flight — don't double-fire
        run_tasks.track(asyncio.create_task(
            run_background_item_skill(ctx, context_id, item_id,
                                       dev_root / "work-items" / item_id,
                                       "investigate", model, effort)))
        return True
    except Exception:
        log.exception("first investigate run failed to start for %s", item_id)
        return False


def fire_auto_triage(context_id: str, item_id: str, spine) -> bool:
    """Kick a freshly-active item into its first triage run.

    Only for an item `active` at `triage` with no run in flight; a failure leaves it resting there."""
    from ....gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if str(item.get("status")) != "active" or str(item.get("phase")) != "triage":
            return False   # only a fresh, active, un-triaged item is kicked
        # Resolve with the item's locked config, so the first kick honours the model chosen at
        # capture.
        model = spine.effective_model(context_id, item_model=item.get("model"))
        effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
        if begin_run(ctx, context_id, item_id, "triage", model, phase="triage") is None:
            return False   # a run is already in flight — don't double-fire
        run_tasks.track(asyncio.create_task(
            _run_background_triage(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                   model, effort)))
        return True
    except Exception:
        log.exception("auto-triage failed to start for %s", item_id)
        return False


# --------------------------------------------------------------------------- deputy send-back re-run

# Which skill a phase re-runs with — its own. One entry per phase that can be re-run in place.
_PHASE_FEEDBACK_SKILL = {"triage": "triage", "plan": "plan", "investigate": "investigate"}

# Phases whose feedback lands somewhere else, because the work must change: `review` is a send-
# back, `build` a `revise`.
_ROUTES_BACK = ("review", "build")

# Per-kind, because the destination must be a phase the kind actually has — research has no plan
# phase.
_SEND_BACK_TARGET = {"implementation": "plan", "research": "investigate"}


def _send_back_phase(item: dict) -> str:
    """Where a `revise` from review or build re-enters, for this item's kind. Unknown kinds route to
    the implementation path rather than raise — losing recourse is the worse failure."""
    return _SEND_BACK_TARGET.get(str(item.get("kind") or ""), "plan")


def fire_phase_feedback(context_id: str, item_id: str, *, phase: str, feedback: str,
                        digest: str | None = None, by: str = "deputy") -> bool:
    """Deliver gate feedback as a real turn on the item's own session, so the phase re-runs in-thread.

    `by` sets attribution only. Routing is identical whoever gives it."""
    from ....gateway import contexts   # lazy: avoid an import cycle at module load
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if not item or item.get("done_at"):
            return False
        if phase not in _PHASE_FEEDBACK_SKILL and phase not in _ROUTES_BACK:
            return False   # not a gate that negotiates via a phase re-run
        # Review feedback falls back into the kind's rework phase; flipping the phase is what
        # makes the rework re-run.
        run_phase = _send_back_phase(item) if phase in _ROUTES_BACK else phase
        skill = _PHASE_FEEDBACK_SKILL.get(run_phase)
        if skill is None:
            return False   # the kind routes somewhere that cannot re-run — never guess a skill
        if phase in _ROUTES_BACK:
            if phase == "review":
                # A send-back spends the approval, so the PR closes with it — the diff it
                # described is about to change.
                from ..git_ops import close_pr
                close_pr(_dev, dev_root, item_id)
            _dev.set_work_item_phase(dev_root, item_id, run_phase)
            # One event per speaker: `gate_briefs` counts these as revision rounds, so a build's
            # conclusion must not log a review.
            kind, said = (("review.route", f"Review feedback routed back to {run_phase}")
                          if phase == "review" else
                          ("revise.route",
                           "Build concluded the plan must change — routed to " + run_phase))
            _dev_store.log_event(context_id, kind, f"{said}: {feedback[:160]}",
                                 item_id=item_id, actor=by,
                                 meta={"from": phase, "to": run_phase, "feedback": feedback[:400],
                                       "by": by})
        # Resume the TARGET phase's own thread — resuming the entering phase's re-plans in the
        # reviewer's head.
        slots = item.get("sessions") or {}
        session_id = (slots.get(kind_profiles.session_slot(run_phase))
                      or slots.get(kind_profiles.LEGACY_INTAKE_SLOT)
                      or item.get("session_id") or None)
        # Claude Code stores sessions per project path, so a detached checkout's session cannot be
        # resumed from the repo root.
        from ..git_ops import ensure_scratch_worktree
        repo_dir = ensure_scratch_worktree(ctx, context_id, item,
                                           dev=_dev, dev_store=_dev_store, spine=_spine)
        if repo_dir != ctx.cwd:
            ctx = replace(ctx, cwd=repo_dir)
        model = _spine.effective_model(context_id, item_model=item.get("model"))
        effort = _spine.effective_effort(context_id, item_effort=item.get("effort"))
        if begin_run(ctx, context_id, item_id, skill, model, phase=run_phase) is None:
            return False   # a run is already in flight — don't double-fire
        # The marker the FE matches to attribute the turn to `<by>`; `speech` is the bubble,
        # `text` is what gets sent.
        title = item.get("title") or item_id
        prompt = kernel_speech.phase_feedback_trigger(item_id, title, run_phase, skill, feedback, digest)
        _dev_store.log_event(context_id, f"{by}.query",
                             f"{by.title()} sent feedback into the {run_phase} thread: {feedback[:160]}",
                             item_id=item_id, actor=by,
                             meta={"phase": run_phase, "origin_gate": phase, "speech": feedback,
                                   "text": prompt, "by": by})
        run_tasks.track(asyncio.create_task(
            _run_deputy_feedback_turn(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                      session_id=session_id, phase=run_phase, prompt=prompt,
                                      model=model, effort=effort)))
        return True
    except Exception:
        log.exception("%s feedback re-run failed to start for %s", by, item_id)
        return False


def fire_deputy_feedback(context_id: str, item_id: str, *, phase: str, feedback: str,
                         digest: str | None = None) -> bool:
    """Back-compat alias — the deputy's send-back path (deputy._do_send_back). See
    `fire_phase_feedback` (by='deputy')."""
    return fire_phase_feedback(context_id, item_id, phase=phase, feedback=feedback,
                               digest=digest, by="deputy")


async def _run_deputy_feedback_turn(ctx, context_id: str, item_id: str, item_dir: Path, *,
                                    session_id: str | None, phase: str, prompt: str,
                                    model: str | None, effort: str | None) -> None:
    """Resume the item's session and let the agent re-run the phase against the feedback.

    Ends at `awaiting_human`, which re-fires the gate so the deputy re-judges. That chaining IS the
    loop."""
    dev_root = ctx.internal_root / "dev"
    capture_prompt(context_id, prompt, item_id=item_id)
    # Re-read the item after any phase flip so the pointer names the phase this re-run actually
    # works.
    live_item = _dev.read_work_item(dev_root, item_id) or {"id": item_id, "phase": phase}
    focus = kernel_speech.work_item_preamble(
        item_id, live_item, str(item_dir), interactive=False,
        compacted_checkpoint=compacted_checkpoint(ctx, live_item, session_id))
    final_tokens = final_usage = final_session = None
    run_started = time.time()
    live = LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    turn = ResilientTurn("deputy feedback", item_id=item_id,
                         notify=retry_notice(context_id, item_id, phase))
    async for ev in turn.stream(
        _agent, ctx, prompt,
        resume=session_id,   # RESUME — the deputy's turn lands in the item's own transcript
        model=model,
        effort=effort or _spine.effective_effort(context_id),
        approve=scoped_writes_approve(item_dir, deny_all),
        sandbox_writes=[item_dir],   # sandboxed shell; the item folder is its one outside write
        extra_mcp_servers={**dev_mcp(ctx, ctx.cwd, item_id,
                                      scope=str(live_item.get("phase") or phase)),
                           "run": make_run_report_server(sink)},
        system_append=focus,
        item_bound=True,       # one item is this run's subject — no board-wide in-progress list
    ):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens, final_usage, final_session = (ev.tokens, ev.usage, ev.session_id)
            _sessions.record(ctx, ev.session_id)
            if ev.session_id:
                try:
                    # The feedback re-run belongs to the phase it re-runs, so it lands in that
                    # phase's slot.
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id,
                                               slot=kind_profiles.session_slot(phase))
                    _spine.stamp_session_item(ev.session_id, item_id)
                except Exception:
                    log.exception("deputy feedback: failed to persist session to %s", item_id)
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    # `ResilientTurn` returns rather than raises, so the closing block below is reached on every
    # path.
    report = await ensure_completion(ctx, context_id, item_id, sink, skill=str(phase),
                                     session_id=final_session, model=model, effort=effort)
    stopped = turn.fault.failed and not report
    if stopped:
        mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase=phase)
    end_run(ctx, context_id, item_id, final_tokens,
             "error" if stopped else "awaiting_human", final_usage,
             outcome="blocked" if stopped else ((report or {}).get("outcome") or UNREPORTED),
             session_id=final_session)
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after deputy feedback failed")
    log.info("deputy feedback re-run: done for %s (%s%s)", item_id, phase,
             f", {turn.fault.kind}" if turn.fault.failed else "")
