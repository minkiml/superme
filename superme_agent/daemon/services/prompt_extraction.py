"""Prompt X-ray — the throwaway prompt-extraction probe engine.

Fire a disposable work-item that runs the REAL lifecycle unattended, so every phase's actual input
prompt is captured; at close it tears ITSELF down, leaving only the tagged run trace.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ...core import git_layer

log = logging.getLogger("superme-agent")

_PROBE_TITLE = "Prompt X-ray probe"
# A repo-agnostic trivial task: it exercises a real build→vet cycle so the captured prompts are
# genuine, but nothing lands.
_PROBE_DESC = (
    "THROWAWAY prompt-extraction probe (auto-generated; torn down at close, never merged). "
    "Create a single new file named `PROMPT_XRAY_PROBE.md` at the repository root whose entire "
    "contents are the one line `probe ok`. Do not modify, rename, or delete any other file. This "
    "item exists only to exercise the workflow so its input prompts can be inspected — its changes "
    "are discarded."
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def launch(context_id: str) -> dict:
    """Mint + fire one throwaway probe for `context_id`, returning the status dict. One probe at a
    time per repo: if one is already running (state says so AND its folder still exists), return that
    instead of minting a second."""
    from .. import app_state
    from ..app_state import get_spine
    from ...gateway import contexts
    from . import runs as _runs
    ctx = contexts.resolve(context_id, "dev")
    if not ctx.internal_root:
        raise ValueError("context has no internal root")
    dev = app_state.dev
    spine = get_spine()
    dev_root = ctx.internal_root / "dev"
    prev = spine.get_prompt_extraction_state(context_id)
    if prev and prev.get("status") == "running" and prev.get("item_id") \
            and dev.read_work_item(dev_root, prev["item_id"]) is not None:
        return status(context_id)                       # a probe is already in flight — reuse it
    created = dev.create_work_item(dev_root, _PROBE_TITLE, _PROBE_DESC,
                                   kind="implementation", autopilot=True, prompt_extraction=True)
    item_id = created["id"]
    # The probe captures prompt SHAPE, so run it on a cheaper tier. Write the alias, not a
    # concrete id.
    dev.set_work_item_model(dev_root, item_id, "sonnet")
    spine.set_prompt_extraction_state(context_id, {
        "item_id": item_id, "status": "running", "started_at": _now(), "finished_at": None})
    app_state.dev_store.log_event(context_id, "prompt_extraction.launch",
                                  "Prompt X-ray probe launched (throwaway — captures each phase's "
                                  "input, then self-destructs)",
                                  item_id=item_id, actor="owner")
    _runs.fire_auto_triage(context_id, item_id, spine)  # first-kick → self-drives to close
    return status(context_id)


def teardown(context_id: str, item_id: str, *, reason: str = "") -> None:
    """Tear a finished probe all the way down, keeping only the tagged run trace.

    Best-effort and step-independent: a partial failure still clears the rest."""
    from .. import app_state
    from ..app_state import get_spine
    from ...gateway import contexts
    try:
        ctx = contexts.resolve(context_id, "dev")
        if not ctx.internal_root:
            return
        dev = app_state.dev
        spine = get_spine()
        dev_root = ctx.internal_root / "dev"
        item = dev.read_work_item(dev_root, item_id) or {}
        # Hard-delete the transcript and resume state; the run trace and its captured inputs
        # survive.
        for sid in dev.work_item_session_ids(item):
            try:
                app_state.sessions.delete(ctx, sid, cause="deleted")
            except Exception:  # noqa: BLE001
                log.exception("probe teardown: session delete failed %s", sid)
        # 2. worktree dir + branch — disposable scaffolding (never merged), NOT trace, so both go.
        try:
            git_layer.remove_worktree(ctx.cwd, ctx.id, item_id)
        except Exception:  # noqa: BLE001
            log.exception("probe teardown: worktree remove failed %s", item_id)
        branch = item.get("git_branch")
        if branch:
            try:
                git_layer.delete_branch(ctx.cwd, str(branch))
            except Exception:  # noqa: BLE001
                log.exception("probe teardown: branch delete failed %s", branch)
        # Cancel the task before removing the folder: a turn still running would recreate it as an
        # orphan shell.
        try:
            from .runs import stop_item_work
            stop_item_work(context_id, item_id)
        except Exception:  # noqa: BLE001
            log.exception("probe teardown: stop_item_work failed %s", item_id)
        # 4. the knowledge folder (item.md + artifacts) — the last on-disk trace.
        try:
            dev.delete_work_item(dev_root, item_id)
        except Exception:  # noqa: BLE001
            log.exception("probe teardown: folder delete failed %s", item_id)
        st = spine.get_prompt_extraction_state(context_id) or {}
        if str(st.get("item_id")) == str(item_id):
            st.update({"status": "done", "finished_at": _now()})
            spine.set_prompt_extraction_state(context_id, st)
        app_state.dev_store.log_event(context_id, "prompt_extraction.teardown",
                                      f"Prompt X-ray probe torn down ({reason})".strip(),
                                      item_id=item_id, actor="daemon", meta={"reason": reason})
        log.info("prompt-extraction probe %s torn down (%s)", item_id, reason)
    except Exception:  # noqa: BLE001
        log.exception("prompt-extraction teardown failed for %s", item_id)


def status(context_id: str) -> dict:
    """The Prompt X-ray tab's read: {running, status, item_id, started_at, finished_at, links}. Each
    link is a captured "A" page for the LAST probe's phase runs (survives the probe's teardown)."""
    from ..app_state import get_spine
    spine = get_spine()
    st = spine.get_prompt_extraction_state(context_id) or {}
    item_id = st.get("item_id")
    links = []
    if item_id:
        for r in spine.list_run_inputs_for_item(str(item_id)):
            links.append({
                "run_id": r["run_id"],
                "phase": r.get("phase"),
                "started_at": r.get("started_at"),
                "url": f"/api/dev/work-items/{item_id}/runs/{r['run_id']}/input.html"
                       f"?context_id={context_id}",
            })
    return {
        "running": st.get("status") == "running",
        "status": st.get("status"),
        "item_id": item_id,
        "started_at": st.get("started_at"),
        "finished_at": st.get("finished_at"),
        "links": links,
    }
