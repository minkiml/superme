"""Did the turn actually finish its job, or only stop talking."""

from ...app_state import agent as _agent, dev_store as _dev_store, spine as _spine
from ....core import deny_all
from ....core import kernel_speech
from ....harness.tools.run_tools import make_run_report_server
from ..turns import ResilientTurn
from .lifecycle import log

def read_completion(context_id: str, item_id: str, sink: dict,
                    run_id: int | None = None) -> dict | None:
    """A run's completion payload out of its `report_completion` sink. None when it never reported.

    `run_id` names the run this report ENDS, stored alongside the payload rather than inside it."""
    report = sink.get("report")
    if report:
        rid = run_id if run_id is not None else _spine.running_run_id(context_id, item_id)
        _dev_store.log_event(context_id, "run.report",
                             f"{report['outcome']}: {report['summary'][:160]}",
                             item_id=item_id, actor="agent", meta={**report, "run_id": rid})
    return report


UNREPORTED = "unreported"   # a run that finished but declared nothing, even after the backstop


async def ensure_completion(ctx, context_id: str, item_id: str, sink: dict, *, skill: str,
                            session_id: str | None, model: str | None, effort: str | None,
                            run_id: int | None = None) -> dict | None:
    """`read_completion` with a BACKSTOP: a run that ended without declaring is asked to.

    The nudge resumes its own session. Never inferred: `outcome` encodes judgment only the agent
    holds."""
    report = read_completion(context_id, item_id, sink, run_id=run_id)
    if report or not session_id:
        if not report:
            log.warning("%s run for %s ended undeclared with no session to resume", skill, item_id)
        return report
    log.info("%s run for %s ended undeclared — asking for its outcome", skill, item_id)
    # `retry=False` deliberately — the work is done and only its label is missing; backoff is the
    # wrong trade.
    turn = ResilientTurn("completion-backstop", item_id=item_id, retry=False)
    try:
        async for _ev in turn.stream(
            _agent, ctx, kernel_speech.completion_nudge(skill),
            resume=session_id, model=model, effort=effort, approve=deny_all,
            extra_mcp_servers={"run": make_run_report_server(sink)},
            item_bound=True,
        ):
            pass
    except Exception:   # noqa: BLE001 — the backstop must never turn a finished run into a failure
        log.exception("completion backstop turn failed for %s (%s)", item_id, skill)
    report = read_completion(context_id, item_id, sink, run_id=run_id)
    if not report:
        log.warning("%s run for %s stayed undeclared after the backstop", skill, item_id)
    return report
