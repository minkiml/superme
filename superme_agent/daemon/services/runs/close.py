"""The close run, and the retry that follows one that could not clear the item."""

import asyncio
import time
from pathlib import Path

from ...app_state import (agent as _agent, dev as _dev, dev_store as _dev_store,
                          sessions as _sessions, spine as _spine)
from ...deps import cache_slash as _cache_slash
from .. import run_tasks
from ....core import (Init, Result, Status, TextDelta, ToolResult, Usage, deny_all,
                      scoped_writes_approve)
from ....core import autopilot as _autopilot
from ....core import kernel_speech
from ....harness.tools.run_tools import make_run_report_server
from ..turns import ResilientTurn
from .lifecycle import (_LiveTokens, _begin_run, _dev_mcp, _end_run, log, mark_item_error,
                        retry_notice)
from .capture import capture_event, capture_prompt, capture_run_input, surface_from_turn
from .checkpoints import bank_auto_checkpoint, compacted_checkpoint
from .completion import ensure_completion

def fire_close_run(context_id: str, item_id: str, spine) -> bool:
    """Fire the ONE closing run of the CLOSE phase — the workflow's only knowledge write.

    Fires only for an item resting at `close` with no run in flight; when none can start it clears
    anyway, with the gap on record."""
    from ....gateway import contexts   # lazy: avoid an import cycle at module load
    ctx = None
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return False
        dev_root = ctx.internal_root / "dev"
        item = _dev.read_work_item(dev_root, item_id) or {}
        if item.get("done_at") or str(item.get("phase")) != "close":
            return False
        model = spine.effective_model(context_id, item_model=item.get("model"))
        effort = spine.effective_effort(context_id, item_effort=item.get("effort"))
        if _begin_run(ctx, context_id, item_id, "close", model, phase="close") is None:
            return False   # a run is already in flight — don't double-fire (it owns the status)
        run_tasks.track(asyncio.create_task(
            _run_background_close(ctx, context_id, item_id, dev_root / "work-items" / item_id,
                                  model, effort)))
        return True
    except Exception:
        log.exception("auto-close failed to start for %s", item_id)
        return False
    finally:
        # No run started and the item still `active` means nothing will move it; clear it, gap
        # recorded.
        try:
            if ctx is not None and ctx.internal_root:
                d_root = ctx.internal_root / "dev"
                it = _dev.read_work_item(d_root, item_id) or {}
                if (not it.get("done_at") and str(it.get("phase")) == "close"
                        and str(it.get("status")) == "active"
                        and not _spine.is_item_running(context_id, item_id)):
                    from .. import clearance
                    clearance.clear_item(
                        context_id, item_id,
                        knowledge_gap="no closing run could start — the anchor docs were "
                                      "not updated")
        except Exception:
            log.exception("close-phase clearance fallback failed for %s", item_id)


async def _run_background_close(ctx, context_id: str, item_id: str, item_dir: Path,
                                model: str | None = None, effort: str | None = None) -> None:
    """Drive the item's ONE closing turn: RESUME its intake thread and let the close skill reflect the
    locked changes into the anchor docs and the change log.

    The kernel clears the item from there — the run never completes it."""
    dev_root = ctx.internal_root / "dev"
    item = _dev.read_work_item(dev_root, item_id) or {}
    session_id = ((item.get("sessions") or {}).get("intake") or item.get("session_id") or None)
    title = item.get("title") or item_id
    prompt = kernel_speech.close_trigger(item_id, title)
    capture_prompt(context_id, prompt, item_id=item_id)
    # The resumed transcript carries the bulk; this is the standing phase/role pointer every
    # runner sends.
    focus = kernel_speech.work_item_preamble(
        item_id, item, str(item_dir), interactive=False,
        compacted_checkpoint=compacted_checkpoint(ctx, item, session_id))
    final_tokens = final_usage = final_session = None
    run_started = time.time()
    live = _LiveTokens()
    sink: dict = {}   # report_completion lands here (run_tools) — read after the turn
    turn = ResilientTurn("auto-close", item_id=item_id,
                         notify=retry_notice(context_id, item_id, "close"))
    # Built once, then both SNAPSHOTTED and SENT — see `surface_from_turn`.
    turn_kwargs = dict(
        resume=session_id,   # RESUME the intake thread — the closeout narrates the whole item
        model=model,
        effort=effort or _spine.effective_effort(context_id),
        approve=scoped_writes_approve(item_dir, deny_all),
        write_boundary=[item_dir],   # the shell boundary, matching the sandbox beside it
        sandbox_writes=[item_dir],   # sandboxed shell; the item folder is its one outside write
        extra_mcp_servers={**_dev_mcp(ctx, ctx.cwd, item_id, scope="close"),
                           "run": make_run_report_server(sink)},
        system_append=focus,
        item_bound=True,       # one item is this run's subject — no board-wide in-progress list
    )
    # Prompt inspector "A" — throwaway probes ONLY: capture matches the real send exactly.
    if _autopilot.is_prompt_extraction(item):
        capture_run_input(context_id, item_id, ctx=ctx, system_append=focus, prompt=prompt,
                          phase="close",
                          surface=surface_from_turn(turn_kwargs, mcp=["dev", "run"]),
                          background=True)
    async for ev in turn.stream(_agent, ctx, prompt, **turn_kwargs):
        if isinstance(ev, Usage):
            live.bump(context_id, item_id, ev)
        elif isinstance(ev, Result):
            final_tokens, final_usage, final_session = (ev.tokens, ev.usage, ev.session_id)
            _sessions.record(ctx, ev.session_id)
            if ev.session_id:
                try:
                    _dev.set_work_item_session(dev_root, item_id, ev.session_id,
                                               slot="close")
                    _spine.stamp_session_item(ev.session_id, item_id)
                except Exception:
                    log.exception("auto-close: failed to persist session to %s", item_id)
        elif isinstance(ev, Init):
            _cache_slash(ctx.id, ev.slash_commands)
        if isinstance(ev, (Status, TextDelta, ToolResult)):
            capture_event(context_id, ev, item_id=item_id)
    report = await ensure_completion(ctx, context_id, item_id, sink, skill="close",
                                     session_id=final_session, model=model, effort=effort)
    outcome = str((report or {}).get("outcome") or "")
    # `active`, never `awaiting_human`: nobody is being paged. Clearance decides next,
    # mechanically.
    stopped = turn.fault.failed and not outcome
    if stopped:
        mark_item_error(ctx, context_id, item_id, turn.fault.reason, phase="close")
    _end_run(ctx, context_id, item_id, final_tokens, "error" if stopped else "active", final_usage,
             outcome="blocked" if stopped else (outcome or None), session_id=final_session)
    try:
        bank_auto_checkpoint(ctx, item_id, since=run_started)
    except Exception:
        log.exception("auto-checkpoint after auto-close failed")
    # A stopped close run must not spend the clearance retry budget, which exists for unreported
    # finishes.
    if not stopped:
        _clear_or_retry(context_id, item_id, outcome)


def _clear_or_retry(context_id: str, item_id: str, outcome: str) -> None:
    """The post-CLOSE kernel hook: reported ⇒ clear the item; not ⇒ re-fire, and once the budget is
    spent clear it anyway with the gap recorded.

    Clearance always completes — a closing run that cannot finish is a SuperMe fault."""
    from .. import clearance
    try:
        if outcome:
            # Close has no authority to change anything, so a non-success outcome is a knowledge
            # gap to record, not a hold.
            gap = None if outcome in ("success", "clean_noop") else \
                f"the closing run reported `{outcome}`"
            res = clearance.clear_item(context_id, item_id, knowledge_gap=gap)
            if not res.get("ok"):
                log.info("close: clearance held for %s — %s", item_id, res.get("refused"))
            return
        tries = clearance.close_retries(context_id, item_id)
        if tries < clearance.MAX_CLOSE_RETRY:
            _dev_store.log_event(
                context_id, "close.retry",
                f"Closing run ended without a report — retry {tries + 1} of "
                f"{clearance.MAX_CLOSE_RETRY}",
                item_id=item_id, actor="daemon", meta={"attempt": tries + 1})
            fire_close_run(context_id, item_id, _spine)
            return
        clearance.clear_item(context_id, item_id,
                             knowledge_gap=f"the closing run ended without a report "
                                           f"{tries + 1} times — the anchor docs were not updated")
    except Exception:
        log.exception("post-close clearance failed for %s", item_id)
