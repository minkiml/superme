"""A run row opening and closing, and the token totals it accumulates while live."""

import asyncio
import logging
from pathlib import Path

from ...app_state import dev as _dev, dev_store as _dev_store, spine as _spine
from .. import run_tasks
from ....core.vocab import sandbox as _sandbox
from ....core.faults import RETRY_LADDER
from ....core.vocab.models import MODEL_TIERS
from ....harness.tools.dev_tools import make_dev_mcp_server

log = logging.getLogger("superme-agent")

# Concrete id, not the `sonnet` alias, which lags behind the latest release.
DEFAULT_RUN_MODEL = MODEL_TIERS["sonnet"]


def stop_item_work(context_id: str, item_id: str, *, expect_live: bool = False) -> tuple[int, bool]:
    """End an item's work for real: cancel the TASK, then close its run ROWS.

    Cancel first, or releasing rows leaves the coroutine writing into a doomed folder."""
    cancelled = run_tasks.cancel(context_id, item_id, expect_live=expect_live)
    freed = _spine.release_item_runs(context_id, item_id)
    if cancelled:
        log.info("stopped live task for %s/%s before releasing %d run row(s)",
                 context_id, item_id, freed)
    return freed, cancelled


def mark_item_error(ctx, context_id: str, item_id: str, reason: str, *, phase: str = "") -> bool:
    """Stop an item at `error`, the one writer of that status.

    The item stays where it died. Never terminal: `error` is what Resume and re-run read."""
    if not (item_id and getattr(ctx, "internal_root", None)):
        return False
    try:
        line = " ".join(str(reason or "").split()) or "the work stopped unexpectedly"
        if not _dev.set_work_item_error(ctx.internal_root / "dev", item_id, line):
            return False
        _dev_store.log_event(
            context_id, "run.error",
            f"Work stopped{f' during {phase}' if phase else ''} — {line[:160]}",
            item_id=item_id, actor="daemon", meta={"phase": phase, "reason": line})
        log.warning("item %s stopped at error%s: %s", item_id,
                    f" ({phase})" if phase else "", line[:160])
        return True
    except Exception:
        log.exception("could not mark %s as error", item_id)
        return False


def retry_notice(context_id: str, item_id: str, phase: str):
    """The trail a retry leaves: a run asleep on the backoff ladder is indistinguishable from a
    hung one."""
    def _notify(fault, attempt: int, delay: int) -> None:
        try:
            _dev_store.log_event(
                context_id, "run.retry",
                f"{phase.capitalize()} paused — {fault.reason}. Retry {attempt} of "
                f"{len(RETRY_LADDER)} in {max(1, delay // 60)} min",
                item_id=item_id, actor="daemon",
                meta={"phase": phase, "kind": fault.kind, "attempt": attempt, "delay": delay})
        except Exception:
            log.exception("retry notice failed for %s", item_id)
    return _notify


def _set_status(ctx, item_id: str, status: str) -> None:
    """Set a work-item's run-state status. Best-effort, logs on failure.

    Never overwrites a typed pause, since resting an `awaiting_child` item would un-pause it."""
    if not (item_id and ctx.internal_root):
        return
    try:
        dev_root = ctx.internal_root / "dev"
        if status == "active":
            cur = _dev.read_work_item(dev_root, item_id) or {}
            if str(cur.get("status")) == "awaiting_child":
                return  # the pause survives the turn's end-of-run rest
        _dev.set_work_item_status(dev_root, item_id, status)
    except Exception:
        log.exception("could not set status %s on %s", status, item_id)


def begin_run(ctx, context_id: str, item_id: str, kind: str = "plan",
               model: str | None = None, phase: str | None = None) -> int | None:
    """Open a run row only if the item is not already running, then rest it `active`.

    The row is both the live state and the run-lock, so a start race cannot lose it."""
    run_id = _spine.start_item_run(context_id, mode=ctx.mode, feature=kind,
                                   item_id=item_id, model=model, phase=phase)
    if run_id is None:
        return None  # already running — no status flip, no event
    # A starting run means the item is being worked; "running now" is derived from the live run
    # row.
    _set_status(ctx, item_id, "active")
    _dev_store.log_event(context_id, f"{kind}.start", f"Started {kind} run",
                         item_id=item_id, actor="daemon", meta={"model": model})
    return run_id  # the live run id — the caller keys its per-run event trail on it


class LiveTokens:
    """Per-run token tally, deduped by `message_id`.

    The SDK emits one Usage step per content block, so summing steps over-counts. `Result.usage` is
    parent-only and misses every subagent call."""

    _KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
             "output_tokens")

    def __init__(self) -> None:
        self._by_msg: dict[str, dict] = {}
        self._legacy: list[dict] = []      # steps from an SDK build with no message_id

    def bump(self, context_id: str, item_id: str, ev) -> None:
        """Record one step. NEVER raises: this runs inside the run's own task, so anything escaping kills
        the WORK."""
        try:
            mid = getattr(ev, "message_id", None)
            step = dict(getattr(ev, "usage", None) or {})
            if mid:
                self._by_msg[mid] = step   # latest wins — usage can grow within one message
            else:
                self._legacy.append(step)
            _spine.set_item_run_tokens(
                context_id, item_id, tokens=self.tokens(), ctx_pct=ev.ctx_pct,
            )
        except Exception:
            log.exception("live token bump failed for %s — the run continues, uncounted", item_id)

    @staticmethod
    def _num(value) -> int:
        """A usage field as an int, or 0 for anything else.

        External data on every step, so an unexpected value must never stop the work."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            if value not in (None, ""):
                log.warning("unusable token value in SDK usage (%r) — counted as 0", value)
            return 0
        return int(value)

    def usage(self, final: dict | None = None) -> dict | None:
        """The whole turn's usage, parent and subagents, or None if none arrived.

        `final` is read for output_tokens alone, which per-message usage only reports as a placeholder."""
        steps = list(self._by_msg.values()) + self._legacy
        if not steps:
            return None
        summed = {k: sum(self._num(s.get(k)) for s in steps) for k in self._KEYS}
        summed["output_tokens"] = max(summed["output_tokens"],
                                      self._num((final or {}).get("output_tokens")))
        return summed

    def tokens(self) -> int:
        """The 3-type scalar (input + cache_creation + output; cache_read excluded) — what the running row
        and the card footer show."""
        u = self.usage() or {}
        return (self._num(u.get("input_tokens")) + self._num(u.get("cache_creation_input_tokens"))
                + self._num(u.get("output_tokens")))


def end_run(ctx, context_id: str, item_id: str, tokens: int | None,
             status: str = "active", usage: dict | None = None,
             ctx_pct: int | None = None, outcome: str | None = None,
             session_id: str | None = None, summary: str = "") -> None:
    """Finalize a run's spine row and rest the work-item.

    A background run ending at a human gate passes `awaiting_human`, the only status that pages."""
    info = _spine.live_run(context_id, item_id)
    kind = (info or {}).get("feature", "plan")
    # The run's PHASE, not its feature — route on this, label the surface with `kind`.
    run_phase = str((info or {}).get("phase") or kind)
    rid = _spine.finish_item_run(context_id, item_id, fallback_tokens=tokens, usage=usage,
                                 ctx_pct=ctx_pct, outcome=outcome, session_id=session_id)
    # The authoritative 3-type total finish just reconciled onto the row; the pre-finish snapshot
    # over-counts.
    total = _spine.run_tokens(rid) if rid else (tokens or 0)
    _set_status(ctx, item_id, status)
    # Every run is offered `scratch/` and most never write there; sweeping must never block
    # finishing a run.
    if (root := getattr(ctx, "internal_root", None)) is not None:
        _sandbox.prune_scratch(Path(root) / "dev" / "work-items" / item_id)
    _dev_store.log_event(context_id, f"{kind}.end", f"Finished {kind} run · Σ {total} tok",
                         item_id=item_id, actor="daemon", meta={"tokens": total})
    # Scheduled, not inline: this run's lock must be fully released before the next phase begins.
    if kind != "compact" and session_id:
        from .. import compaction
        compaction.note_turn_start(session_id)
    # Only a review run routes here — a build's `revise` belongs to its own driver.
    if outcome == "revise" and run_phase == "review":
        # local: phases fires runs, and a run ending is what routes a revise back to it.
        from .phases import fire_phase_feedback
        try:
            fire_phase_feedback(context_id, item_id, phase="review",
                                feedback=summary or "the review concluded this needs re-planning",
                                by="owner")
        except Exception:
            log.exception("revise routing failed for %s (item stays at review)", item_id)
        return
    if outcome == "revise":
        return   # the phase's driver routes it — see loop.background_build_cycle
    if status == "awaiting_human" and outcome != "needs_user":
        try:
            from ..gates import maybe_autopilot_advance
            asyncio.get_running_loop().call_soon(
                maybe_autopilot_advance, context_id, item_id)
        except RuntimeError:
            pass  # no running loop (sync/test context) — driver is tested directly


def dev_mcp(ctx, repo_dir: Path, item_id: str, *, scope: str) -> dict:
    """The dev MCP server for a background intake or resolve run.

    `scope` names which tools this run sees, so a close run cannot hold plan's pens."""
    from .phases import fire_auto_triage      # local: phases fires the runs this server serves
    return {"dev": make_dev_mcp_server(
        _dev_store, ctx.id, spine=_spine, scope=scope,
        dev_root=ctx.internal_root / "dev",
        repo_dir=repo_dir, main_repo_dir=ctx.cwd,
        bound_item_id=item_id,
        fire_triage=lambda child_id: fire_auto_triage(ctx.id, child_id, _spine),
    )}
